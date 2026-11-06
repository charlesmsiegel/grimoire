"""Played chat turns (035): one player post, the takes generated for it, and what the table kept.

A turn is transcript, never world state: nothing here is an event, a belief, an effect or a ruling. A
kept line is what the table chose to show; it changes no fiction a later rule could read as canon.

Who heard what is fixed when a line is spoken (``audience``), so an actor that joins a chat later was
in no earlier audience and hears nothing that came before. Narration is heard by the table (the
present player characters), never by an NPC. Withdrawn and struck lines are shown to no one.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field, replace

from grimoire.comms import CHANNELS
from grimoire.core.identity import ChatRef, InstanceRef, SourceRef, TurnRef, validate_segment
from grimoire.core.inference import TOKEN_FIELDS, CallAttempt, CallId, InferenceStage, TokenUsage

#: How a turn opened: a player's post, the table asking the cast to go on (no post), a card or scenario's
#: opening, or (036, not written here) history imported from another app.
ORIGINS = ("post", "prompt", "opening", "imported")
#: ``running``: written before any call, so a crash leaves it (``interrupted`` on open). ``stale``: a newer
#: run claimed the chat; its calls were billed but its lines are dropped. ``superseded``: a running take a
#: later run replaced.
TAKE_STATES = ("running", "proposed", "kept", "withdrawn", "stale", "superseded")
LINE_STATES = ("shown", "withdrawn", "struck")
POST_KINDS = ("speech", "action")
#: 027's call outcomes, and ``planned`` for a call of a running take that has not ended.
CALL_STATES = ("planned", "accepted", "rejected", "incomplete", "failed", "cancelled", "refused", "not_issued")
TURN_OUTCOMES = ("complete", "halted", "cancelled", "stale")
#: Takes per turn: rerolls are cheap to ask for, and each one is stored for good.
MAX_TAKES = 16
#: 032's channels that reach only their addressees.
_PRIVATE = frozenset({"whisper", "telepathy"})
#: A turn id is ``<chat_id>.<number:06d>``, and the post's message id is the turn id, which 032 caps at 104.
MAX_CHAT_ID = 97
MAX_TEXT = 20_000
MAX_NUMBER = 999_999
#: Room for 107's principals, which are opaque text.
MAX_PRINCIPAL = 200


class SceneRefused(ValueError):
    """A scene operation that cannot be made; nothing was written. ``reason`` is machine-readable."""

    def __init__(self, reason: str, message: str = "") -> None:
        super().__init__(f"{reason}: {message}" if message else reason)
        self.reason = reason

    def __reduce__(self) -> tuple[object, ...]:
        return (type(self), (self.reason,))


def _fail(reason: str, message: str = "") -> SceneRefused:
    return SceneRefused(reason, message)


def _text(value: object, label: str, *, limit: int = MAX_TEXT, blank: bool = False) -> str:
    if type(value) is not str or len(value) > limit or (not blank and not value.strip()):
        raise _fail("invalid_field", f"{label} must be {'' if blank else 'nonblank '}text of at most {limit} chars")
    return value


def _optional(value: object, label: str, limit: int = MAX_TEXT) -> str | None:
    return None if value is None else _text(value, label, limit=limit)


def _actors(values: Iterable[object], label: str, campaign: str) -> tuple[InstanceRef, ...]:
    found = tuple(values)
    if not all(type(value) is InstanceRef and value.campaign_id == campaign for value in found):
        raise _fail("invalid_field", f"{label} must be this campaign's actors")
    if len(set(found)) != len(found):
        raise _fail("invalid_field", f"{label} names an actor twice")
    return tuple(sorted(found, key=str))


def turn_id(chat: ChatRef, number: int) -> str:
    """``<chat_id>.<number:06d>``: campaign-unique, and the id of the turn's post and its message."""
    if len(chat.chat_id) > MAX_CHAT_ID:
        raise _fail("chat_id_too_long", f"a played chat's id is at most {MAX_CHAT_ID} characters")
    if type(number) is not int or not 1 <= number <= MAX_NUMBER:
        raise _fail("too_many_turns", f"a chat has at most {MAX_NUMBER} turns")
    return f"{chat.chat_id}.{number:06d}"


@dataclass(frozen=True, slots=True)
class Post:
    """What a player wrote for their character, as read: who heard it is fixed here.

    ``channel`` is 032's channel for a message (``speech``, ``whisper``...), or ``None`` for an action that
    is not a message. ``withheld`` marks an action 032 could not rule out as a private message (a note, a
    whispered aside, a signal): its sender alone hears it, and it is in no one else's transcript, the table's
    included. ``submitted_by`` is the authenticated principal, never the director.
    """

    submitted_by: str
    actor: InstanceRef
    kind: str
    text: str
    audience: tuple[InstanceRef, ...]
    channel: str | None = None
    addressees: tuple[InstanceRef, ...] = ()
    withheld: bool = False

    def __post_init__(self) -> None:
        _text(self.submitted_by, "submitted_by", limit=MAX_PRINCIPAL)
        if self.submitted_by == "director":
            raise _fail("invalid_field", "the director never posts for a player")
        if type(self.actor) is not InstanceRef:
            raise _fail("invalid_field", "a post's actor is an InstanceRef")
        if self.kind not in POST_KINDS:
            raise _fail("invalid_field", f"a post is one of {POST_KINDS}")
        _text(self.text, "text")
        if self.channel is not None and self.channel not in CHANNELS:
            raise _fail("invalid_field", f"a message's channel is one of {CHANNELS}")
        if self.channel is None and (self.kind == "speech" or self.addressees):
            raise _fail("invalid_field", "a message has a channel; only a visible action has none")
        campaign = self.actor.campaign_id
        object.__setattr__(self, "audience", _actors(self.audience, "audience", campaign))
        object.__setattr__(self, "addressees", _actors(self.addressees, "addressees", campaign))
        if self.channel in _PRIVATE and not self.addressees:
            raise _fail("invalid_field", "a whisper or a telepathic message names who it is for")
        if self.actor not in self.audience:
            raise _fail("invalid_field", "a post's speaker hears it")
        if type(self.withheld) is not bool:
            raise _fail("invalid_field", "withheld is true or false")
        if self.withheld and (self.kind != "action" or self.channel is not None or self.audience != (self.actor,)):
            raise _fail("invalid_field", "a withheld post is an action its sender alone hears")

    @property
    def private(self) -> bool:
        """A message for its addressees only, or a withheld action: never in the table's transcript."""
        return self.channel in _PRIVATE or self.withheld


@dataclass(frozen=True, slots=True)
class SceneLine:
    """One generated line. ``actor`` is ``None`` for the narrator, whose lines only the table hears.

    ``by`` is who withdrew or struck it (a principal), ``direction`` the request's guidance.
    """

    line_id: str
    actor: InstanceRef | None
    speaker: str
    text: str
    audience: tuple[InstanceRef, ...]
    state: str = "shown"
    by: str | None = None
    actor_revision: SourceRef | None = None
    voice_profile: SourceRef | None = None
    review: str = "unlabelled"

    def __post_init__(self) -> None:
        validate_line_id(self.line_id)
        if self.actor is not None and type(self.actor) is not InstanceRef:
            raise _fail("invalid_field", "a line's actor is an InstanceRef or None")
        _text(self.speaker, "speaker", limit=2000)
        _text(self.text, "text")
        if self.state not in LINE_STATES:
            raise _fail("invalid_field", f"a line is one of {LINE_STATES}")
        if (self.by is None) != (self.state == "shown"):
            raise _fail("invalid_field", "a withdrawn or struck line names who did it; a shown one does not")
        _optional(self.by, "by", MAX_PRINCIPAL)
        for label in ("actor_revision", "voice_profile"):
            value = getattr(self, label)
            if value is not None and not (type(value) is SourceRef and value.pinned):
                raise _fail("invalid_field", f"{label} is a pinned SourceRef")
        if self.review != "unlabelled":
            raise _fail("invalid_field", "no labeller reviews a line yet: every line is unlabelled")
        if self.audience:
            object.__setattr__(self, "audience", _actors(self.audience, "audience", self.audience[0].campaign_id))

    @property
    def shown(self) -> bool:
        return self.state == "shown"


def validate_line_id(value: object) -> None:
    try:
        validate_segment(value, "line_id")
    except ValueError as error:
        raise _fail("invalid_field", str(error)) from None


@dataclass(frozen=True, slots=True)
class CallRecord:
    """One planned model call of a take: its outcome and every attempt the turn's usage ledger booked."""

    phase: str
    stage: InferenceStage
    actor_id: str | None
    ordinal: int
    status: str
    reason: str | None = None
    attempts: tuple[CallAttempt, ...] = ()

    def __post_init__(self) -> None:
        if self.status not in CALL_STATES:
            raise _fail("invalid_field", f"a call's status is one of {CALL_STATES}")
        _optional(self.reason, "reason", 200)
        object.__setattr__(self, "attempts", tuple(self.attempts))
        if not all(type(attempt) is CallAttempt for attempt in self.attempts):
            raise _fail("invalid_field", "attempts are CallAttempt values")
        call = self.attempts[0].call_id if self.attempts else None
        if call is not None and any((a.call_id.phase, a.call_id.stage, a.call_id.actor_id, a.call_id.ordinal)
                                    != (self.phase, self.stage, self.actor_id, self.ordinal) or a.call_id != call
                                    for a in self.attempts):
            raise _fail("invalid_field", "a call's attempts are all its own")

    def call_id(self, campaign_id: str, plan_turn: str) -> CallId:
        return CallId(campaign_id, plan_turn, self.phase, self.stage, self.actor_id, self.ordinal)


@dataclass(frozen=True, slots=True)
class Take:
    """One generation for a turn (or its opening), with its calls, cast and what became of it.

    ``plan_turn`` is the id the generation was planned and billed under (``<turn_id>.<take>``), so two
    takes of one turn never share a call id. ``unscreened`` names director lore no reply screen covered
    (027): keeping such a take needs the table to acknowledge it.
    """

    take: int
    state: str
    origin: str
    cast: tuple[InstanceRef, ...]
    lines: tuple[SceneLine, ...] = ()
    calls: tuple[CallRecord, ...] = ()
    outcome: str | None = None
    declined: tuple[str, ...] = ()
    unscreened: tuple[str, ...] = ()
    direction: str | None = None
    by: str | None = None

    def __post_init__(self) -> None:
        if type(self.take) is not int or not 1 <= self.take <= MAX_TAKES:
            raise _fail("invalid_field", f"a take is numbered 1..{MAX_TAKES}")
        if self.state not in TAKE_STATES:
            raise _fail("invalid_field", f"a take is one of {TAKE_STATES}")
        if self.origin not in ("generated", "opening"):
            raise _fail("invalid_field", "a take is generated or an opening")
        object.__setattr__(self, "lines", tuple(self.lines))
        object.__setattr__(self, "calls", tuple(self.calls))
        if not all(type(line) is SceneLine for line in self.lines):
            raise _fail("invalid_field", "a take's lines are SceneLine values")
        if not all(type(call) is CallRecord for call in self.calls):
            raise _fail("invalid_field", "a take's calls are CallRecord values")
        if len({line.line_id for line in self.lines}) != len(self.lines):
            raise _fail("invalid_field", "a take repeats a line id")
        if self.state in ("running", "stale", "superseded") and self.lines:
            raise _fail("invalid_field", f"a {self.state} take holds no lines")
        if self.cast:
            object.__setattr__(self, "cast", _actors(self.cast, "cast", self.cast[0].campaign_id))
        for label in ("declined", "unscreened"):
            values = tuple(getattr(self, label))
            if not all(type(value) is str and value for value in values):
                raise _fail("invalid_field", f"{label} are nonblank texts")
            object.__setattr__(self, label, values)
        if self.outcome is not None and self.outcome not in TURN_OUTCOMES:
            raise _fail("invalid_field", f"a take's outcome is one of {TURN_OUTCOMES}")
        _optional(self.direction, "direction")
        _optional(self.by, "by", MAX_PRINCIPAL)

    def shown(self) -> tuple[SceneLine, ...]:
        return tuple(line for line in self.lines if line.shown)

    def attempts(self) -> tuple[CallAttempt, ...]:
        return tuple(attempt for call in self.calls for attempt in call.attempts)


@dataclass(frozen=True, slots=True)
class TurnRecord:
    """One turn of one chat: ``post`` (or none), its takes, and whether the table closed it.

    Open while no take is kept and nobody passed it. While the last turn is ``paused`` no new turn is
    posted, begun or generated; who paused is kept for ``resume`` only and never shown.
    """

    chat: ChatRef
    number: int
    origin: str
    post: Post | None = None
    takes: tuple[Take, ...] = ()
    paused: bool = False
    paused_by: str | None = None
    passed_by: str | None = None
    extras: dict[str, object] = field(default_factory=dict, compare=False)

    def __post_init__(self) -> None:
        if type(self.chat) is not ChatRef:
            raise _fail("invalid_field", "a turn's chat is a ChatRef")
        turn_id(self.chat, self.number)
        if self.origin not in ORIGINS:
            raise _fail("invalid_field", f"a turn's origin is one of {ORIGINS}")
        if (self.post is not None) != (self.origin == "post"):
            raise _fail("invalid_field", "a post turn has a post; no other turn does")
        if self.post is not None and self.post.actor.campaign_id != self.chat.campaign_id:
            raise _fail("invalid_field", "a post's actor belongs to the turn's campaign")
        object.__setattr__(self, "takes", tuple(self.takes))
        if not all(type(take) is Take for take in self.takes):
            raise _fail("invalid_field", "a turn's takes are Take values")
        if [take.take for take in self.takes] != list(range(1, len(self.takes) + 1)):
            raise _fail("invalid_field", "takes are numbered 1, 2, ... in order")
        if sum(take.state == "kept" for take in self.takes) > 1:
            raise _fail("invalid_field", "a turn keeps at most one take")
        if sum(take.state == "running" for take in self.takes) > 1:
            raise _fail("invalid_field", "a turn runs at most one take")
        if type(self.paused) is not bool or (self.paused_by is None) == self.paused:
            raise _fail("invalid_field", "a paused turn names who paused it; an unpaused one does not")
        _optional(self.paused_by, "paused_by", MAX_PRINCIPAL)
        _optional(self.passed_by, "passed_by", MAX_PRINCIPAL)
        if self.passed_by is not None and self.kept is not None:
            raise _fail("invalid_field", "a turn is either kept or passed")
        prefix = self.turn_id + "."
        campaign = self.chat.campaign_id
        if self.post is not None and any(actor.campaign_id != campaign for actor in self.post.audience):
            raise _fail("invalid_field", "a post's audience belongs to the turn's campaign")
        for take in self.takes:
            if any(actor.campaign_id != campaign for actor in take.cast) or any(
                    actor.campaign_id != campaign for line in take.lines for actor in line.audience):
                raise _fail("invalid_field", "a take's cast and audiences belong to the turn's campaign")
            if any(a.call_id.campaign_id != self.chat.campaign_id or a.call_id.turn_id != self.plan_turn(take.take)
                   for a in take.attempts()):
                raise _fail("invalid_field", "a take's calls were planned under <turn_id>.<take>")
            for line in take.lines:
                if not line.line_id.startswith(f"{prefix}{take.take}."):
                    raise _fail("invalid_field", "a line id is <turn_id>.<take>.<n>")
                if line.actor is not None and line.actor.campaign_id != self.chat.campaign_id:
                    raise _fail("invalid_field", "a line's actor belongs to the turn's campaign")

    @property
    def turn_id(self) -> str:
        return turn_id(self.chat, self.number)

    @property
    def ref(self) -> TurnRef:
        return TurnRef(self.chat.campaign_id, self.turn_id)

    @property
    def kept(self) -> Take | None:
        return next((take for take in self.takes if take.state == "kept"), None)

    @property
    def running(self) -> Take | None:
        return next((take for take in self.takes if take.state == "running"), None)

    @property
    def open(self) -> bool:
        return self.kept is None and self.passed_by is None

    def plan_turn(self, take: int) -> str:
        """The id a take's generation is planned and billed under."""
        return f"{self.turn_id}.{take}"

    def with_take(self, take: Take) -> TurnRecord:
        """The turn with ``take`` added, or replacing the take of the same number."""
        takes = [existing for existing in self.takes if existing.take != take.take]
        takes.append(take)
        return replace(self, takes=tuple(sorted(takes, key=lambda each: each.take)))

    def visible(self, actor: InstanceRef | None) -> tuple[SceneLine, ...]:
        """Lines shown on this turn's kept take that ``actor`` heard (every one for ``None``). Proposals
        are heard by no one: a reroll must not hear the take it replaces."""
        take = self.kept
        return tuple(line for line in (take.shown() if take else ()) if actor is None or actor in line.audience)


def usage_totals(attempts: Iterable[CallAttempt]) -> dict[str, int]:
    """Summed reported tokens, by field, and how many attempts left each field unreported."""
    sums = {name: 0 for name in TOKEN_FIELDS}
    unreported = {name: 0 for name in TOKEN_FIELDS}
    for attempt in attempts:
        for name in TOKEN_FIELDS:
            value = getattr(attempt.usage, name)
            if value is None:
                unreported[name] += 1
            else:
                sums[name] += value
    return {**sums, **{f"unreported_{name}": unreported[name] for name in TOKEN_FIELDS}}


__all__ = [
    "CALL_STATES", "CallRecord", "LINE_STATES", "MAX_CHAT_ID", "MAX_TAKES", "ORIGINS", "POST_KINDS", "Post",
    "SceneLine", "SceneRefused", "TAKE_STATES", "Take", "TokenUsage", "TurnRecord", "turn_id", "usage_totals",
]
