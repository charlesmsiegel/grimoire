"""Session zero: negotiated content and participation boundaries.

A deterministic policy over *labelled* narration segments, separate from
player sovereignty (who may author a PC) and adjudication (what happened).
Boundaries change how the fiction is presented and which proposals may go
ahead; nothing here can create, edit or delete accepted canon.

Boundaries, changes, requests and consents come only from the player-input
channel, with the authenticated speaker as setter/requester. Themes come from a
labeller that is not the narrator. Anonymity holds only when the caller passes
the authenticated scene audience and viewer.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field, replace

from .authority import DIRECTOR, _choice
from .model import _nonempty

TREATMENTS = ("allow", "check_in", "fade_to_black", "skip", "avoid")  # ascending protection
SCOPES = ("setting", "world", "campaign", "player")
UNSET = "unset"
CHANGE_KINDS = ("set", "withdraw", "agree", "unagree")
SEGMENT_STATUSES = ("proposed", "accepted")
REQUEST_KINDS = ("pause", "skip", "redirect", "rephrase")

_THEME = re.compile(r"[a-z0-9]+(?:[_-][a-z0-9]+)*")
_RANK = {treatment: rank for rank, treatment in enumerate(TREATMENTS)}
_PROPOSED_ACTION = {"allow": "present", "check_in": "check_in", "fade_to_black": "fade_to_black",
                    "skip": "withhold_proposal", "avoid": "withhold_proposal"}
_ACCEPTED_ACTION = {"allow": "present", "check_in": "check_in", "fade_to_black": "fade_to_black",
                    "skip": "skip", "avoid": "skip"}
_NOTICE = {("proposed", "skip"): "skipped_proposal", ("proposed", "avoid"): "avoided_theme",
           ("accepted", "avoid"): "canon_touches_avoided_theme"}
_LIMIT_FAILURES = ("avoided", "needs_check_in", "unset", "not_agreed")  # worst first


class BoundaryAuthorityError(Exception):
    """A refused boundary change. One message, so a refusal reveals no hidden id.

    Not an `OSError`, for the same reason as `AuthorityError`.
    """

    def __init__(self) -> None:
        super().__init__("not your boundary")


def _theme(value: object, label: str = "theme") -> None:
    if not isinstance(value, str) or _THEME.fullmatch(value) is None:
        raise ValueError(f"{label} must be a lowercase slug")


def _themes(values: Iterable[str], label: str = "theme") -> frozenset[str]:
    if isinstance(values, str):
        raise ValueError(f"{label}s must be a collection, not a string")
    values = frozenset(values)
    for value in values:
        _theme(value, label)
    return values


def _person(value: object, label: str) -> None:
    _nonempty(value, label)
    if value == DIRECTOR:
        raise ValueError(f"{label} cannot be the director")


def _people(values: Iterable[str], label: str) -> frozenset[str]:
    if isinstance(values, str):
        raise ValueError(f"{label} must be a collection, not a string")
    values = frozenset(values)
    for value in values:
        _person(value, label)
    return values


def _strictest(treatments: Iterable[str], default: str = UNSET) -> str:
    return max(treatments, key=_RANK.__getitem__, default=default)


# --- configuration -------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class Boundary:
    """One theme's treatment at one scope. `agreed_by` is for group scopes only."""

    boundary_id: str
    scope: str
    scope_id: str
    theme: str
    treatment: str
    set_by: str
    attributed: bool = False
    agreed_by: frozenset[str] = frozenset()
    private_note: str = field(default="", repr=False)

    def __post_init__(self) -> None:
        _nonempty(self.boundary_id, "boundary_id")
        _choice(self.scope, SCOPES, "scope")
        _person(self.scope_id, "scope_id")
        _theme(self.theme)
        _choice(self.treatment, TREATMENTS, "treatment")
        _person(self.set_by, "set_by")
        if not isinstance(self.attributed, bool):
            raise ValueError("attributed must be a bool")
        if not isinstance(self.private_note, str):
            raise ValueError("private_note must be a string")
        agreed = _people(self.agreed_by, "agreed_by")
        if self.scope == "player":
            if self.set_by != self.scope_id:
                raise ValueError("a player sets only their own boundaries")
            if agreed:
                raise ValueError("a player boundary has no group agreement")
        else:
            agreed |= {self.set_by}
        object.__setattr__(self, "agreed_by", agreed)


@dataclass(frozen=True, slots=True)
class BoundaryChange:
    """A player's set/withdraw of their own boundary, or agree/unagree to a group one."""

    kind: str
    requested_by: str
    boundary: Boundary | None = None
    boundary_id: str | None = None

    def __post_init__(self) -> None:
        _choice(self.kind, CHANGE_KINDS, "change kind")
        _person(self.requested_by, "requested_by")
        if self.kind == "set":
            if not isinstance(self.boundary, Boundary) or self.boundary_id is not None:
                raise ValueError("set takes a boundary, not a boundary_id")
        else:
            if self.boundary is not None:
                raise ValueError(f"{self.kind} takes a boundary_id, not a boundary")
            _nonempty(self.boundary_id, "boundary_id")


@dataclass(frozen=True, slots=True, repr=False)
class SessionZero:
    """An immutable, optional configuration. Empty means nothing configured."""

    boundaries: tuple[Boundary, ...] = ()

    def __post_init__(self) -> None:
        boundaries = tuple(self.boundaries)
        if not all(isinstance(b, Boundary) for b in boundaries):
            raise ValueError("boundaries must be Boundary values")
        if len({b.boundary_id for b in boundaries}) != len(boundaries):
            raise ValueError("duplicate boundary ids")
        object.__setattr__(self, "boundaries", boundaries)

    def __repr__(self) -> str:
        return f"SessionZero({len(self.boundaries)} boundaries)"

    def apply(self, change: BoundaryChange) -> SessionZero:
        """Return the configuration after one player's change; ownership is checked first."""
        if not isinstance(change, BoundaryChange):
            raise ValueError("expected a BoundaryChange")
        who = change.requested_by
        items = list(self.boundaries)
        key = change.boundary.boundary_id if change.kind == "set" else change.boundary_id
        index = next((i for i, b in enumerate(items) if b.boundary_id == key), None)
        old = None if index is None else items[index]

        if change.kind == "set":
            new = change.boundary
            if new.set_by != who or (old is not None and old.set_by != who):
                raise BoundaryAuthorityError()
            if new.agreed_by - {who}:
                raise ValueError("only the setter's own agreement can be recorded")
            if old is None:
                return SessionZero((*items, new))
            if (new.scope, new.scope_id, new.theme) != (old.scope, old.scope_id, old.theme):
                raise ValueError("scope and theme are fixed; withdraw and set a new boundary")
            if new.treatment == old.treatment:
                new = replace(new, agreed_by=old.agreed_by)
            items[index] = new
        elif change.kind == "withdraw":
            if old is None or old.set_by != who:
                raise BoundaryAuthorityError()
            del items[index]
        else:
            if old is None or old.scope == "player" or (
                    change.kind == "unagree" and old.set_by == who):
                raise BoundaryAuthorityError()
            agreed = old.agreed_by | {who} if change.kind == "agree" else old.agreed_by - {who}
            items[index] = replace(old, agreed_by=agreed)
        return SessionZero(tuple(items))


@dataclass(frozen=True, slots=True)
class BoundaryContext:
    """Where and to whom fiction is presented. `audience` must be authenticated."""

    campaign_id: str
    audience: frozenset[str]
    world_id: str | None = None
    setting_id: str | None = None

    def __post_init__(self) -> None:
        _nonempty(self.campaign_id, "campaign_id")
        audience = _people(self.audience, "audience")
        if not audience:
            raise ValueError("audience must name at least one participant")
        object.__setattr__(self, "audience", audience)
        if self.world_id is not None:
            _nonempty(self.world_id, "world_id")
        if self.setting_id is not None:
            _nonempty(self.setting_id, "setting_id")


def _applicable(config: SessionZero, context: BoundaryContext,
                players: frozenset[str] = frozenset()) -> tuple[Boundary, ...]:
    """Boundaries in force here; unresolvable world/setting scopes fail closed."""
    scoped = {"setting": context.setting_id, "world": context.world_id,
              "campaign": context.campaign_id}
    for scope in ("world", "setting"):
        if scoped[scope] is None and any(b.scope == scope for b in config.boundaries):
            raise ValueError(f"context has no {scope}_id but {scope} boundaries exist")
    players = context.audience | players
    return tuple(b for b in config.boundaries
                 if (b.scope_id in players if b.scope == "player"
                     else b.scope_id == scoped[b.scope]))


def _same_campaign(items: tuple, campaign_id: str, label: str) -> None:
    if any(item.campaign_id != campaign_id for item in items):
        raise ValueError(f"{label} from another campaign")


# --- rulings and visibility ------------------------------------------------------

@dataclass(frozen=True, slots=True)
class VisibleBoundary:
    """What any participant sees. Owner fields are None unless attributed."""

    scope: str
    theme: str
    treatment: str
    scope_id: str | None
    set_by: str | None
    boundary_id: str | None


@dataclass(frozen=True, slots=True)
class ThemeRuling:
    """The most protective treatment for a theme, or `unset` when none applies."""

    theme: str
    treatment: str
    sources: tuple[VisibleBoundary, ...]
    conflict: bool


def _visible(b: Boundary) -> VisibleBoundary:
    shown = b.attributed
    return VisibleBoundary(b.scope, b.theme, b.treatment,
                           b.scope_id if shown or b.scope != "player" else None,
                           b.set_by if shown else None, b.boundary_id if shown else None)


def _order(v: VisibleBoundary) -> tuple:
    """Sorted on visible fields only, so order never hints at a hidden owner."""
    return (v.theme, SCOPES.index(v.scope), _RANK[v.treatment],
            v.scope_id or "", v.set_by or "", v.boundary_id or "")


def _ruling(theme: str, applicable: tuple[Boundary, ...]) -> ThemeRuling:
    sources = tuple(sorted((_visible(b) for b in applicable if b.theme == theme), key=_order))
    treatments = {s.treatment for s in sources}
    return ThemeRuling(theme, _strictest(treatments), sources, len(treatments) > 1)


def rule_themes(config: SessionZero, context: BoundaryContext,
                themes: Iterable[str]) -> tuple[ThemeRuling, ...]:
    applicable = _applicable(config, context)
    return tuple(_ruling(theme, applicable) for theme in sorted(_themes(themes)))


def visible_boundaries(config: SessionZero,
                       context: BoundaryContext) -> tuple[VisibleBoundary, ...]:
    """Every boundary in force, anonymised; give these themes to the labeller."""
    return tuple(sorted((_visible(b) for b in _applicable(config, context)), key=_order))


def owner_boundaries(config: SessionZero, viewer: str) -> tuple[Boundary, ...]:
    """The authenticated viewer's own boundaries in full, private notes included."""
    _person(viewer, "viewer")
    return tuple(b for b in config.boundaries if b.set_by == viewer)


# --- presentation --------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class NarrationSegment:
    """One labelled piece of proposed narration or depiction of accepted canon.

    `themes=None` is unlabelled; an empty set is labelled with no themes.
    """

    campaign_id: str
    segment_id: str
    text: str
    themes: frozenset[str] | None
    status: str
    event_id: str | None = None

    def __post_init__(self) -> None:
        for label in ("campaign_id", "segment_id", "text"):
            _nonempty(getattr(self, label), label)
        if self.themes is not None:
            object.__setattr__(self, "themes", _themes(self.themes))
        _choice(self.status, SEGMENT_STATUSES, "segment status")
        if (self.status == "accepted") != (self.event_id is not None):
            raise ValueError("accepted segments, and only they, name their event_id")
        if self.event_id is not None:
            _nonempty(self.event_id, "event_id")


@dataclass(frozen=True, slots=True)
class CheckInConsent:
    """One participant's consent to depict `theme` for one segment or condition."""

    campaign_id: str
    target_id: str
    theme: str
    given_by: str

    def __post_init__(self) -> None:
        _nonempty(self.campaign_id, "campaign_id")
        _nonempty(self.target_id, "target_id")
        _theme(self.theme)
        _person(self.given_by, "given_by")


@dataclass(frozen=True, slots=True)
class SegmentDecision:
    """How to present one segment. There is deliberately no field that edits canon."""

    segment_id: str
    action: str
    rulings: tuple[ThemeRuling, ...]
    notices: tuple[str, ...]
    preserved_event_id: str | None


def decide_presentation(config: SessionZero, context: BoundaryContext,
                        segments: Iterable[NarrationSegment],
                        consents: Iterable[CheckInConsent] = ()) -> tuple[SegmentDecision, ...]:
    """Most protective treatment per segment; accepted canon is only ever re-presented."""
    segments, consents = tuple(segments), tuple(consents)
    applicable = _applicable(config, context)
    _same_campaign(segments, context.campaign_id, "segment")
    _same_campaign(consents, context.campaign_id, "consent")
    if len({s.segment_id for s in segments}) != len(segments):
        raise ValueError("duplicate segment ids")

    def decide(s: NarrationSegment) -> SegmentDecision:
        if s.themes is None:
            if any(_RANK[b.treatment] > _RANK["allow"] for b in applicable):
                return SegmentDecision(s.segment_id, "hold_unlabelled", (), ("unlabelled",),
                                       s.event_id)
            return SegmentDecision(s.segment_id, "present", (), (), s.event_id)
        rulings = tuple(_ruling(theme, applicable) for theme in sorted(s.themes))
        notices: list[str] = []
        effective = []
        for ruling in rulings:
            treatment = "allow" if ruling.treatment == UNSET else ruling.treatment
            if treatment == "check_in" and context.audience <= {
                    c.given_by for c in consents
                    if c.target_id == s.segment_id and c.theme == ruling.theme}:
                treatment = "allow"
                notices.append("check_in_cleared")
            effective.append(treatment)
        strictest = _strictest(effective, "allow")
        action = (_ACCEPTED_ACTION if s.status == "accepted" else _PROPOSED_ACTION)[strictest]
        if (s.status, strictest) in _NOTICE:
            notices.append(_NOTICE[s.status, strictest])
        return SegmentDecision(s.segment_id, action, rulings, tuple(dict.fromkeys(notices)),
                               s.event_id)

    return tuple(decide(s) for s in segments)


# --- mid-session requests --------------------------------------------------------

@dataclass(frozen=True, slots=True)
class BoundaryRequest:
    """A participant's pause/skip/redirect/rephrase. No reason is ever asked for.

    `direction` is player-channel guidance like a directorial request: it
    authorizes nothing and cannot change a resolution.
    """

    campaign_id: str
    request_id: str
    requested_by: str
    kind: str
    segment_ids: tuple[str, ...] = ()
    direction: str = ""

    def __post_init__(self) -> None:
        _nonempty(self.campaign_id, "campaign_id")
        _nonempty(self.request_id, "request_id")
        _person(self.requested_by, "requested_by")
        _choice(self.kind, REQUEST_KINDS, "request kind")
        if not isinstance(self.segment_ids, tuple):
            raise ValueError("segment_ids must be a tuple")
        for segment_id in self.segment_ids:
            _nonempty(segment_id, "segment_id")
        if len(set(self.segment_ids)) != len(self.segment_ids):
            raise ValueError("duplicate target segments")
        if (self.kind == "pause") == bool(self.segment_ids):
            raise ValueError("pause takes no targets; skip, redirect and rephrase need them")
        if not isinstance(self.direction, str):
            raise ValueError("direction must be a string")
        if self.direction and self.kind not in ("redirect", "rephrase"):
            raise ValueError("only redirect and rephrase take a direction")


@dataclass(frozen=True, slots=True)
class RequestDecision:
    """Presentation effects of a request: segment ids, plus the event ids it preserves."""

    request_id: str
    status: str
    reason: str
    halt: bool = False
    skip_presentation: tuple[str, ...] = ()
    withdrawn_proposals: tuple[str, ...] = ()
    rerender: tuple[str, ...] = ()
    preserved_events: tuple[str, ...] = ()
    direction: str = ""


def decide_request(request: BoundaryRequest, context: BoundaryContext,
                   segments: Iterable[NarrationSegment]) -> RequestDecision:
    """The same answer for any participant; withdrawals touch proposals only."""
    segments = tuple(segments)
    _same_campaign(segments, context.campaign_id, "segment")
    found = {s.segment_id: s for s in segments}
    if len(found) != len(segments):
        raise ValueError("duplicate segment ids")
    def refuse(reason: str) -> RequestDecision:
        return RequestDecision(request.request_id, "refused", reason)

    if request.campaign_id != context.campaign_id:
        return refuse("cross_campaign")
    if request.requested_by not in context.audience:
        return refuse("not_participant")
    if any(segment_id not in found for segment_id in request.segment_ids):
        return refuse("unknown_segment")

    targets = [found[segment_id] for segment_id in request.segment_ids]
    accepted = tuple(s.segment_id for s in targets if s.status == "accepted")
    proposed = tuple(s.segment_id for s in targets if s.status == "proposed")
    preserved = tuple(s.event_id for s in targets if s.status == "accepted")

    def accept(**effects) -> RequestDecision:
        return RequestDecision(request.request_id, "accepted", "accepted", **effects)

    if request.kind == "pause":
        return accept(halt=True)
    if request.kind == "skip":
        return accept(skip_presentation=accepted, withdrawn_proposals=proposed,
                      preserved_events=preserved)
    if request.kind == "redirect":
        return accept(withdrawn_proposals=proposed, preserved_events=preserved,
                      direction=request.direction)
    return accept(rerender=request.segment_ids, preserved_events=preserved,
                  direction=request.direction)


# --- agreed limits for imposed conditions ---------------------------------------

@dataclass(frozen=True, slots=True)
class LimitCheck:
    within: bool
    reason: str


def agreed_limits(themes: Iterable[str], config: SessionZero, context: BoundaryContext, *,
                  controller: str | None, target_id: str,
                  consents: Iterable[CheckInConsent] = ()) -> LimitCheck:
    """Whether an imposed condition's themes are within limits the affected player agreed.

    The rule/effect writer passes `within` into `ImposedCondition`. Nothing is
    cached: recompute on every review so a tightened boundary applies at once.
    """
    themes, consents = _themes(themes), tuple(consents)
    if not themes:
        raise ValueError("a condition is labelled with at least one theme")
    _nonempty(target_id, "target_id")
    _same_campaign(consents, context.campaign_id, "consent")
    if controller is None:
        raise ValueError("an uncontrolled actor has no agreed limits")
    applicable = _applicable(config, context, frozenset({controller}))
    if controller == DIRECTOR:
        return LimitCheck(True, "npc")
    _person(controller, "controller")
    consented = {c.theme for c in consents
                 if c.target_id == target_id and c.given_by == controller}

    def failure(theme: str) -> str | None:
        relevant = [b for b in applicable if b.theme == theme]
        treatment = _strictest(b.treatment for b in relevant)
        if treatment == "avoid":
            return "avoided"
        if treatment == UNSET:
            return "unset"
        if treatment == "check_in":
            return None if theme in consented else "needs_check_in"
        agreed = any(b.scope_id == controller if b.scope == "player"
                     else controller in b.agreed_by for b in relevant)
        return None if agreed else "not_agreed"

    failures = {failure(theme) for theme in themes} - {None}
    worst = next((reason for reason in _LIMIT_FAILURES if reason in failures), None)
    return LimitCheck(False, worst) if worst else LimitCheck(True, "agreed")
