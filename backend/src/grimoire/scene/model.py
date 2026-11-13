"""Played chat turns (035): one player post, the takes generated for it, and what the table kept.

A turn is transcript, never world state: nothing here is an event, a belief, an effect or a ruling. A
kept line is what the table chose to show; it changes no fiction a later rule could read as canon.

Who heard what is fixed when a line is spoken (``audience``), so an actor that joins a chat later was
in no earlier audience and hears nothing that came before. Narration is heard by the table (the
present player characters), never by an NPC. Withdrawn and struck lines are shown to no one.
"""

from __future__ import annotations


import hashlib
import re
from collections.abc import Iterable
from dataclasses import dataclass, field, replace

from .identity import contribution_id

from grimoire.adjudication import MAX_FACTORS, MAX_STAKES, Factor, Stake
from grimoire.comms import CHANNELS
from grimoire.core.identity import ChatRef, InstanceRef, SourceRef, TurnRef, validate_segment
from grimoire.core.inference import TOKEN_FIELDS, CallAttempt, CallId, InferenceStage, TokenUsage
from grimoire.effects import EffectProposal, EndorsementRecord
from grimoire.goals import MAX_APPROACHES, MAX_CONDITIONS, MAX_GOALS, MAX_LIMITS, OUTCOMES

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
#: A labelling of a turn's action: ``running`` (written before its call, so a crash leaves the planned call on record),
#: ``proposed`` (labels to confirm), ``refused`` (the call or its reply gave no labels), ``superseded`` (a running
#: or proposed labelling a newer one replaced) and ``framed`` (recorded as the action's 029 frame).
LABEL_STATES = ("running", "proposed", "refused", "superseded", "framed")
#: Labellings per turn: each is a metered call, kept for good.
MAX_LABELS = 8
#: A turn's proposed 021 effect (035 milestone 2): ``proposed`` (keys to collect), ``approved`` (the director's
#: outcome key given), ``accepting`` (every key held and an event id allocated: written before the commit, so a
#: crash leaves it for open-time recovery), ``accepted`` (the event is in force), ``refused`` (021 or 029 refused
#: it, or recovery aborted it unpublished), ``superseded`` (a newer proposal replaced it) and ``revoked``
#: (recovery aborted it after something landed, or its accepted event was revoked through 021 since).
EFFECT_STATES = ("proposed", "approved", "accepting", "accepted", "refused", "superseded", "revoked")
#: Effect proposals per turn, kept for good.
MAX_EFFECTS = 8
#: Takes per turn: rerolls are cheap to ask for, and each one is stored for good.
MAX_TAKES = 16
#: A recorded 039 intention (035 milestone 2) follows 039's own caps: every approach of an agenda is weighed.
INTENTION_OUTCOMES = OUTCOMES
MAX_INTENTION_OPTIONS = MAX_GOALS * MAX_APPROACHES
MAX_INTENTION_CONDITIONS = MAX_CONDITIONS
MAX_INTENTION_LIMITS = MAX_LIMITS
#: A condition as ``describe()`` gives it: an actor reference of three 128-character segments, or a proposition id
#: and its stances, with room to spare.
MAX_CONDITION_CHARS = 1000
#: ``explain`` gives one or two lines; each quotes at most an actor, ids, an aim and an action (400 characters each)
#: or the conditions a wish lacks (8 at most).
MAX_EXPLANATION = 4
MAX_EXPLANATION_CHARS = MAX_CONDITIONS * MAX_CONDITION_CHARS + 2000
#: 032's channels that reach only their addressees.
_PRIVATE = frozenset({"whisper", "telepathy"})
#: A turn id is ``<chat_id>.<number:06d>`` (``-<device>`` on a library with a device identity), and the post's
#: message id is the turn id, which 032 caps at 104: 97 + 7 untagged, 88 + 7 + 9 tagged. An untagged turn keeps
#: the cap it was written under, so turns saved before device tags still read; a chat whose id is over 88
#: characters takes no new turn once its library has a device identity.
MAX_CHAT_ID = 97
MAX_TAGGED_CHAT_ID = 88
MAX_TEXT = 20_000
MAX_NUMBER = 999_999
#: Room for 107's principals, which are opaque text.
MAX_PRINCIPAL = 200
#: 028's device tag in a turn id: the first eight hex digits of the SHA-256 of the device id (``device_tag``).
_DEVICE = re.compile(r"[0-9a-f]{8}")
_NUMBER = re.compile(r"(?P<number>[0-9]{6})(?:-(?P<device>[0-9a-f]{8}))?")


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


def turn_id(chat: ChatRef, number: int, device: str | None = None) -> str:
    """``<chat_id>.<number:06d>``, or ``<chat_id>.<number:06d>-<device>`` for a turn a library with a device
    identity (028) created: campaign-unique, and the id of the turn's post and its message. Two paired devices
    that each play turn n of a chat offline write two turns with two ids, never one id with two contents."""
    limit = MAX_CHAT_ID if device is None else MAX_TAGGED_CHAT_ID
    if len(chat.chat_id) > limit:
        raise _fail("chat_id_too_long", f"a played chat's id is at most {limit} characters"
                    + ("" if device is None else " on a library with a device identity"))
    if type(number) is not int or not 1 <= number <= MAX_NUMBER:
        raise _fail("too_many_turns", f"a chat has at most {MAX_NUMBER} turns")
    if device is not None and (type(device) is not str or not _DEVICE.fullmatch(device)):
        raise _fail("invalid_field", "a turn's device is eight lowercase hex digits (028's device tag)")
    return f"{chat.chat_id}.{number:06d}" + ("" if device is None else f"-{device}")


def parse_turn_id(value: str) -> tuple[str, int, str | None] | None:
    """``(chat_id, number, device)`` of a turn id, or ``None`` for text that does not spell one."""
    chat_id, _, last = value.rpartition(".")
    found = _NUMBER.fullmatch(last)
    if not chat_id or found is None:
        return None
    return chat_id, int(found["number"]), found["device"]


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
    (027): keeping such a take needs the table to acknowledge it. ``on_stage`` is the part of ``cast`` a 041
    stage had physically present when the take was written (the cast less the stage's absences), or ``None``
    for a take written with no stage (every take before stages existed): then the whole cast was present.
    ``players``, written only beside ``on_stage``, is the player characters by control when the take was
    written (every narrator line's audience): who heard a line as a player character is judged by it, never
    by control now. A staged take without it (an earlier build's) is in no one's table view.
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
    on_stage: tuple[InstanceRef, ...] | None = None
    players: tuple[InstanceRef, ...] | None = None

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
        if self.on_stage is not None:
            if isinstance(self.on_stage, (str, bytes)) or not isinstance(self.on_stage, (tuple, list)):
                raise _fail("invalid_field", "a take's on-stage cast is a list of actors")
            if self.on_stage and not self.cast:
                raise _fail("invalid_field", "a take's on-stage cast is part of its cast")
            here = _actors(self.on_stage, "on_stage", self.cast[0].campaign_id) if self.on_stage else ()
            if not set(here) <= set(self.cast):
                raise _fail("invalid_field", "a take's on-stage cast is part of its cast")
            object.__setattr__(self, "on_stage", here)
        if self.players is not None:
            if isinstance(self.players, (str, bytes)) or not isinstance(self.players, (tuple, list)):
                raise _fail("invalid_field", "a take's player characters are a list of actors")
            if self.on_stage is None or (self.players and not self.cast):
                raise _fail("invalid_field", "a take records its player characters only on a stage, from its cast")
            table = _actors(self.players, "players", self.cast[0].campaign_id) if self.players else ()
            if not set(table) <= set(self.cast) or any(
                    line.actor is None and set(line.audience) != set(table) for line in self.lines):
                raise _fail("invalid_field", "a take's player characters are of its cast, and its narration's audience")
            object.__setattr__(self, "players", table)
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

    @property
    def present(self) -> tuple[InstanceRef, ...]:
        """Who was physically present for this take: its stage's ``on_stage``, else (no stage) its whole cast."""
        return self.cast if self.on_stage is None else self.on_stage

    def attempts(self) -> tuple[CallAttempt, ...]:
        return tuple(attempt for call in self.calls for attempt in call.attempts)


@dataclass(frozen=True, slots=True)
class SetAside:
    """028's choice at a fork, written on a branch head that does not go on: the chat goes on from ``chosen``
    (the id of another turn of the same number, after the same turn), as ``by`` (a principal) chose. The head
    and every turn played after it are set aside: kept, heard by no one, outside the chat's numbering."""

    chosen: str
    by: str

    def __post_init__(self) -> None:
        if type(self.chosen) is not str or parse_turn_id(self.chosen) is None:
            raise _fail("invalid_field", "a set-aside mark names the turn the chat goes on from")
        _text(self.by, "set_aside.by", limit=MAX_PRINCIPAL)


@dataclass(frozen=True, slots=True)
class Against:
    """The 040 obstacle a labelling frames its action against, and the approach the labeller chose for it.

    ``approach_id`` is one of the obstacle's suggestions, or, with ``novel``, an approach it never suggested (the
    runner names it ``novel``; its text is the action and its risks the labelling's stakes). It is ``None`` while
    the labelling runs, and on one refused or superseded before a reply chose."""

    obstacle_id: str
    approach_id: str | None = None
    novel: bool = False

    def __post_init__(self) -> None:
        for value, label in ((self.obstacle_id, "against.obstacle"), (self.approach_id, "against.approach")):
            if value is None and label == "against.approach":
                continue
            if type(value) is not str:
                raise _fail("invalid_field", f"{label} must be an id")
            try:
                validate_segment(value, label)
            except ValueError as error:
                raise _fail("invalid_field", str(error)) from None
        if type(self.novel) is not bool or (self.novel and self.approach_id is None):
            raise _fail("invalid_field", "against.novel is a bool, and a novel approach is named")


#: A due change's id: ``<cost|stake>.<consequence id>.<n>``, its ``n``th authored change (040's ``changes``).
_DUE_CHANGE = re.compile(r"(cost|stake)\.(.+)\.([1-9][0-9]?)")
#: At most this many waivers on one effect (one attempt makes at most 8 stakes and 4 costs due, two changes each).
MAX_WAIVERS = 24


def due_change_id(kind: str, consequence_id: str, index: int) -> str:
    """The id of the ``index``th (from 1) change of the consequence a 040 attempt made due."""
    return f"{kind}.{consequence_id}.{index}"


@dataclass(frozen=True, slots=True)
class Waiver:
    """The director's decision that one change a 040 obstacle attempt made due is not applied by this effect.

    ``change`` is its id (``due_change_id``); ``reason`` says why, in a few words (the relation already holds,
    the actor cannot pay, the character changed hands). The obstacle's own record of what came due is unchanged."""

    change: str
    reason: str

    def __post_init__(self) -> None:
        found = _DUE_CHANGE.fullmatch(self.change) if type(self.change) is str else None
        try:
            if found is None:
                raise ValueError("a waiver names a due change: <cost|stake>.<consequence id>.<n>")
            validate_segment(found.group(2), "waiver.change")
        except ValueError as error:
            raise _fail("invalid_field", str(error)) from None
        if type(self.reason) is not str or not self.reason.strip() or len(self.reason) > 200:
            raise _fail("invalid_field", "a waiver gives its reason in at most 200 characters")


@dataclass(frozen=True, slots=True)
class Label:
    """One labelling of a turn's action for 029: the facts that bear on it and what is at stake (035 M2).

    ``sequence`` is the campaign sequence the action is framed at: every factor was established before it.
    Factors and stakes are proposals read from a labeller's reply on an untrusted handle; a factor's text,
    sequence and source were offered to the labeller, never written by it. ``reason`` says why a ``refused``
    labelling gave no labels (a call status, or ``grimoire.labelling``'s refusal). ``by`` is who asked.
    ``against`` names the 040 obstacle it frames the action against, when the press named one: its factors then
    hold the obstacle's facts that bear on the chosen approach, and its stakes the approach's risks.
    """

    label: int
    state: str
    sequence: int
    factors: tuple[Factor, ...] = ()
    stakes: tuple[Stake, ...] = ()
    calls: tuple[CallRecord, ...] = ()
    reason: str | None = None
    by: str | None = None
    against: Against | None = None

    def __post_init__(self) -> None:
        if type(self.label) is not int or not 1 <= self.label <= MAX_LABELS:
            raise _fail("invalid_field", f"a labelling is numbered 1..{MAX_LABELS}")
        if self.state not in LABEL_STATES:
            raise _fail("invalid_field", f"a labelling is one of {LABEL_STATES}")
        if type(self.sequence) is not int or self.sequence < 1:
            raise _fail("invalid_field", "an action is framed at a positive sequence")
        object.__setattr__(self, "factors", tuple(self.factors))
        object.__setattr__(self, "stakes", tuple(self.stakes))
        object.__setattr__(self, "calls", tuple(self.calls))
        if not all(type(factor) is Factor for factor in self.factors) or len(self.factors) > MAX_FACTORS:
            raise _fail("invalid_field", f"a labelling holds at most {MAX_FACTORS} Factor values")
        if not all(type(stake) is Stake for stake in self.stakes) or len(self.stakes) > MAX_STAKES:
            raise _fail("invalid_field", f"a labelling holds at most {MAX_STAKES} Stake values")
        if len({f.factor_id for f in self.factors}) != len(self.factors) or len(
                {s.stake_id for s in self.stakes}) != len(self.stakes):
            raise _fail("invalid_field", "a labelling repeats a factor or stake id")
        if any(factor.established >= self.sequence for factor in self.factors):
            raise _fail("invalid_field", "a factor was established before the action")
        if self.state in ("running", "refused") and (self.factors or self.stakes):
            raise _fail("invalid_field", f"a {self.state} labelling holds no labels")
        if not all(type(call) is CallRecord and call.stage is InferenceStage.LABELLING for call in self.calls):
            raise _fail("invalid_field", "a labelling's calls are labelling CallRecord values")
        _optional(self.reason, "reason", 200)
        _optional(self.by, "by", MAX_PRINCIPAL)
        if self.against is not None:
            if type(self.against) is not Against:
                raise _fail("invalid_field", "against is an Against value")
            chosen = self.against.approach_id is not None
            if (self.state == "running" and chosen) or (self.state in ("proposed", "framed") and not chosen):
                raise _fail("invalid_field", "an approach is chosen by the reply: none while running, one once "
                                             "proposed or framed")

    def attempts(self) -> tuple[CallAttempt, ...]:
        return tuple(attempt for call in self.calls for attempt in call.attempts)


@dataclass(frozen=True, slots=True)
class TurnEffect:
    """One structured 021 effect proposed for a turn, and the keys collected for it (035 milestone 2).

    The proposal is kept whole, so a restart can present it again (030's obligation). ``endorsers`` are the
    ``(contribution_id, principal)`` pairs 021 requires an endorsement from (a player character's voluntary
    act), worked out when it was proposed; ``endorsements`` are those given so far, each over the proposal's
    fingerprint. ``approved_by`` is the director who gave the outcome key. ``event_id`` names the accepted event
    once the acceptance is under way (``accepting``) or done. ``reason`` says why it was refused or revoked.
    ``controls_at`` is the position in the campaign's sovereignty ledger (003/010b) whose control the acceptance
    was authorized under (the seq of its latest control change, 0 for none), recorded with ``accepting`` so a
    recovery replays that control from the ledger rather than today's, and kept once the effect is ``accepted``
    (and if it is later ``revoked``) as a record of that control. ``None`` on every other state, on an effect
    accepted as a duplicate (nothing was published), and on an acceptance recorded before it existed.
    ``accepted_on`` is the tag of the device whose library started the acceptance (028's device tag, as on its
    turns), recorded and kept with ``controls_at``: 021's files of it are on that device (sync does not carry
    them), so only that device settles it. ``None`` where ``controls_at`` is, and on a library with no device
    identity; an effect without it is read as this device's.
    ``waivers`` are the due changes of the turn's 040 obstacle attempt this effect does not apply, each the
    director's decision with its reason (only on the director's proposal, sorted by change). They are part of
    what is shown and keyed (``fingerprint``); an effect without them is shown by its proposal's fingerprint.
    """

    number: int
    state: str
    proposal: EffectProposal
    endorsers: tuple[tuple[str, str], ...] = ()
    approved_by: str | None = None
    endorsements: tuple[EndorsementRecord, ...] = ()
    event_id: str | None = None
    reason: str | None = None
    controls_at: int | None = None
    accepted_on: str | None = None
    waivers: tuple[Waiver, ...] = ()

    def __post_init__(self) -> None:
        if type(self.number) is not int or not 1 <= self.number <= MAX_EFFECTS:
            raise _fail("invalid_field", f"an effect is numbered 1..{MAX_EFFECTS}")
        if self.state not in EFFECT_STATES:
            raise _fail("invalid_field", f"an effect is one of {EFFECT_STATES}")
        if type(self.proposal) is not EffectProposal:
            raise _fail("invalid_field", "an effect holds an EffectProposal")
        waivers = tuple(self.waivers) if isinstance(self.waivers, (list, tuple)) else None
        if waivers is None or not all(type(item) is Waiver for item in waivers):
            raise _fail("invalid_field", "waivers are Waiver values")
        changes = [item.change for item in waivers]
        if changes != sorted(set(changes)) or len(waivers) > MAX_WAIVERS:
            raise _fail("invalid_field", f"waivers name each due change once, in order, at most {MAX_WAIVERS}")
        if waivers and self.proposal.proposed_by != "director":
            raise _fail("invalid_field", "only the director's proposal waives a due change")
        object.__setattr__(self, "waivers", waivers)
        endorsers = tuple(tuple(pair) for pair in self.endorsers)
        if not all(len(pair) == 2 and all(type(part) is str and part.strip() for part in pair) for pair in endorsers):
            raise _fail("invalid_field", "endorsers are (contribution id, principal) pairs")
        if list(endorsers) != sorted(set(endorsers)) or len({cid for cid, _ in endorsers}) != len(endorsers):
            raise _fail("invalid_field", "endorsers name each contribution once, in order")
        object.__setattr__(self, "endorsers", endorsers)
        endorsements = tuple(self.endorsements)
        if not all(type(item) is EndorsementRecord for item in endorsements):
            raise _fail("invalid_field", "endorsements are EndorsementRecord values")
        given = [(item.contribution_id, item.principal) for item in endorsements]
        if given != sorted(set(given)) or not set(given) <= set(endorsers):
            raise _fail("invalid_field", "an endorsement is one endorser's, once, in order")
        if any(item.fingerprint != self.proposal.fingerprint for item in endorsements):
            raise _fail("invalid_field", "an endorsement is over the proposal's fingerprint")
        object.__setattr__(self, "endorsements", endorsements)
        _optional(self.approved_by, "approved_by", MAX_PRINCIPAL)
        _optional(self.reason, "reason", 200)
        if self.event_id is not None:
            try:
                validate_segment(self.event_id, "event_id")
            except ValueError as error:
                raise _fail("invalid_field", str(error)) from None
        if self.state == "proposed" and self.approved_by is not None:
            raise _fail("invalid_field", "a proposed effect is not approved yet")
        if self.state in ("approved", "accepting") and self.approved_by is None:
            raise _fail("invalid_field", "an approved effect names its director")
        if self.state in ("accepting", "accepted", "revoked") and not self.complete:
            raise _fail("invalid_field", "an effect is accepted only with every key")
        if (self.event_id is not None) != (self.state in ("accepting", "accepted", "revoked")):
            raise _fail("invalid_field", "an accepting, accepted or revoked effect names its event; no other does")
        if self.reason is not None and self.state not in ("refused", "revoked"):
            raise _fail("invalid_field", "only a refused or revoked effect has a reason")
        if self.state == "refused" and self.reason is None:
            raise _fail("invalid_field", "a refused effect says why")
        if self.controls_at is not None and (type(self.controls_at) is not int or self.controls_at < 0):
            raise _fail("invalid_field", "controls_at is a sovereignty ledger position (0 or more)")
        if self.controls_at is not None and self.event_id is None:
            raise _fail("invalid_field", "only an accepting, accepted or revoked effect records its control")
        if self.accepted_on is not None and (type(self.accepted_on) is not str
                                             or not _DEVICE.fullmatch(self.accepted_on)):
            raise _fail("invalid_field", "accepted_on is a device tag (eight hex digits)")
        if self.accepted_on is not None and self.event_id is None:
            raise _fail("invalid_field", "only an accepting, accepted or revoked effect records its device")

    @property
    def fingerprint(self) -> str:
        """What a player is shown and endorses: the proposal's 021 fingerprint, with the waivers when it has any
        (each change and a digest of its reason), so an endorsement covers exactly what it waives. 021 records
        the endorsement over the proposal's own fingerprint; the authority's record of it is over this one."""
        if not self.waivers:
            return self.proposal.fingerprint
        lines = ["035 effect with waivers", self.proposal.fingerprint]
        lines += [f"waive={item.change}|{hashlib.sha256(item.reason.encode('utf-8')).hexdigest()}"
                  for item in self.waivers]
        return "sha256:" + hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()

    @property
    def complete(self) -> bool:
        """Whether every key is held: the director's approval and each required endorsement."""
        return self.approved_by is not None and len(self.endorsements) == len(self.endorsers)

    @property
    def live(self) -> bool:
        """Still collecting keys (``proposed`` or ``approved``): a newer proposal supersedes it."""
        return self.state in ("proposed", "approved")


def _segment_id(value: object, label: str) -> str:
    if type(value) is not str:
        raise _fail("invalid_field", f"{label} must be an id")
    try:
        validate_segment(value, label)
    except ValueError:
        raise _fail("invalid_field", f"{label} must be a lowercase id segment") from None
    return value


def _texts(values: object, label: str, *, limit: int, count: int) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        raise _fail("invalid_field", f"{label} must be a list of texts")
    try:
        found = tuple(values)  # type: ignore[arg-type]
    except TypeError:
        raise _fail("invalid_field", f"{label} must be a list of texts") from None
    if len(found) > count:
        raise _fail("invalid_field", f"{label} holds at most {count} texts")
    return tuple(_text(value, label, limit=limit) for value in found)


@dataclass(frozen=True, slots=True)
class IntentionOption:
    """One approach as 039's policy weighed it for a take: ``outcome`` is its ``Considered`` outcome, ``unmet``
    the conditions that did not hold (as ``describe()`` gives them) and ``forbidden_by`` the limits forbidding it."""

    goal_id: str
    approach_id: str
    outcome: str
    unmet: tuple[str, ...] = ()
    forbidden_by: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _segment_id(self.goal_id, "intention.goal_id")
        _segment_id(self.approach_id, "intention.approach_id")
        if self.outcome not in INTENTION_OUTCOMES:
            raise _fail("invalid_field", f"an approach's outcome is one of {INTENTION_OUTCOMES}")
        object.__setattr__(self, "unmet", _texts(self.unmet, "unmet", limit=MAX_CONDITION_CHARS,
                                                 count=MAX_INTENTION_CONDITIONS))
        if isinstance(self.forbidden_by, (str, bytes)):
            raise _fail("invalid_field", "forbidden_by must be a list of limit ids")
        forbidden = tuple(_segment_id(limit, "forbidden_by") for limit in self.forbidden_by)
        if len(set(forbidden)) != len(forbidden) or len(forbidden) > MAX_INTENTION_LIMITS:
            raise _fail("invalid_field", f"forbidden_by names at most {MAX_INTENTION_LIMITS} limits, once each")
        object.__setattr__(self, "forbidden_by", forbidden)
        if (self.outcome == "unmet") != bool(self.unmet) or (self.outcome == "forbidden") != (
                bool(forbidden) and not self.unmet):
            raise _fail("invalid_field", "an unmet approach names what is unmet, a forbidden one what forbids it")


@dataclass(frozen=True, slots=True)
class Intention:
    """039's choice for one director NPC in one take, as the runner briefed it (035 milestone 2).

    A record of a ``grimoire.goals.Choice``, never one itself: the agenda stays the director's input and is
    never read back from a turn. ``as_of`` is the knowledge sequence it was chosen from; ``present`` the cast as
    the NPC recognised it and ``unrecognised`` the rest of those present for the take (``Take.present``: with a
    stage, its on-stage cast; without one, its whole cast); ``goal_id`` and ``approach_id`` what
    it pursued (``None`` for nothing it could pursue); ``refusals`` the limits it was refusing, ``considered``
    the approaches in the order tried, up to the chosen one, then those after it that a limit forbids (``omitted``
    counts the others after it, outranked or unmet, which are not recorded, so a turn file stays small) and
    ``explanation`` 039's ``explain`` lines. ``perceived``, on a take played on a 041 stage, is who of the take's
    scene the NPC perceived when it chose (041's sight; ``None`` without a stage): whoever it did not is
    ``unrecognised``, so keeping the take re-checks it against the stage then. Director-side text: it names
    actors and propositions by id, so it is in no NPC's window and not in the table's view.
    """

    take: int
    actor: InstanceRef
    as_of: int
    present: tuple[InstanceRef, ...]
    unrecognised: tuple[InstanceRef, ...] = ()
    goal_id: str | None = None
    approach_id: str | None = None
    refusals: tuple[str, ...] = ()
    considered: tuple[IntentionOption, ...] = ()
    explanation: tuple[str, ...] = ()
    omitted: int = 0
    perceived: tuple[InstanceRef, ...] | None = None

    def __post_init__(self) -> None:
        if type(self.take) is not int or not 1 <= self.take <= MAX_TAKES:
            raise _fail("invalid_field", f"an intention is for a take numbered 1..{MAX_TAKES}")
        if type(self.actor) is not InstanceRef:
            raise _fail("invalid_field", "an intention's actor is an InstanceRef")
        if type(self.as_of) is not int or self.as_of < 0:
            raise _fail("invalid_field", "an intention was chosen as of a knowledge sequence (0 or more)")
        campaign = self.actor.campaign_id
        present = _actors(self.present, "intention.present", campaign)
        unseen = _actors(self.unrecognised, "intention.unrecognised", campaign)
        if self.actor not in present or set(present) & set(unseen):
            raise _fail("invalid_field", "an intention's NPC is in its recognised cast, and no one is in both")
        object.__setattr__(self, "present", present)
        object.__setattr__(self, "unrecognised", unseen)
        if self.perceived is not None:
            if not isinstance(self.perceived, (tuple, list)):
                raise _fail("invalid_field", "an intention's perceived cast is a list of actors")
            perceived = _actors(self.perceived, "intention.perceived", campaign)
            if not set(present) <= set(perceived) <= set(present) | set(unseen):
                raise _fail("invalid_field", "an intention's NPC perceived everyone it recognised, and only its scene")
            object.__setattr__(self, "perceived", perceived)
        considered = tuple(self.considered)
        if not considered or len(considered) > MAX_INTENTION_OPTIONS or not all(
                type(option) is IntentionOption for option in considered):
            raise _fail("invalid_field", f"an intention weighs 1..{MAX_INTENTION_OPTIONS} IntentionOption values")
        if len({(o.goal_id, o.approach_id) for o in considered}) != len(considered):
            raise _fail("invalid_field", "an intention weighs each approach once")
        object.__setattr__(self, "considered", considered)
        chosen = [option for option in considered if option.outcome == "chosen"]
        if len(chosen) > 1:
            raise _fail("invalid_field", "an intention chooses at most one approach")
        pursued = (chosen[0].goal_id, chosen[0].approach_id) if chosen else (None, None)
        if (self.goal_id, self.approach_id) != pursued:
            raise _fail("invalid_field", "an intention pursues the approach it chose, or nothing")
        # Recorded: every approach tried up to the chosen one, and the forbidden ones after it (they are what it
        # refuses); the rest after the choice (outranked, or unmet) are only counted, in ``omitted``.
        after = considered[considered.index(chosen[0]) + 1:] if chosen else ()
        if any(option.outcome == "outranked" for option in considered) or any(
                option.outcome != "forbidden" for option in after):
            raise _fail("invalid_field", "after its choice an intention records only the approaches it refuses")
        if type(self.omitted) is not int or self.omitted < 0 or (self.omitted and not chosen) or (
                len(considered) + self.omitted > MAX_INTENTION_OPTIONS):
            raise _fail("invalid_field", "an intention omits only approaches weighed after its choice")
        if isinstance(self.refusals, (str, bytes)):
            raise _fail("invalid_field", "refusals must be a list of limit ids")
        refusals = tuple(_segment_id(limit, "refusals") for limit in self.refusals)
        active = {limit for option in considered if option.outcome == "forbidden" for limit in option.forbidden_by}
        if len(set(refusals)) != len(refusals) or set(refusals) != active:
            raise _fail("invalid_field", "an intention's refusals are the limits forbidding what it could do")
        object.__setattr__(self, "refusals", refusals)
        explanation = _texts(self.explanation, "explanation", limit=MAX_EXPLANATION_CHARS, count=MAX_EXPLANATION)
        if not explanation:
            raise _fail("invalid_field", "an intention is explained")
        object.__setattr__(self, "explanation", explanation)

    @property
    def acts(self) -> bool:
        return self.approach_id is not None

    @property
    def scene(self) -> frozenset[InstanceRef]:
        """The take's whole cast, recognised or not: the scene the choice was made for."""
        return frozenset(self.present) | frozenset(self.unrecognised)


@dataclass(frozen=True, slots=True)
class TurnRecord:
    """One turn of one chat: ``post`` (or none), its takes, and whether the table closed it.

    Open while no take is kept and nobody passed it. While the last turn is ``paused`` no new turn is
    posted, begun or generated; who paused is kept for ``resume`` only and never shown. ``device`` is the
    tag of the device that created it (028), or ``None`` for a turn created on a library with no identity;
    a turn keeps it for good. ``follows`` is the id of the turn it was played after (``None`` for turn 1, and
    for a turn written before 028's turn merge, whose place is inferred), so two devices' branches stay apart
    after either chooses one. ``set_aside`` is 028's mark on a branch head that does not go on.
    """

    chat: ChatRef
    number: int
    origin: str
    post: Post | None = None
    takes: tuple[Take, ...] = ()
    paused: bool = False
    paused_by: str | None = None
    passed_by: str | None = None
    device: str | None = None
    follows: str | None = None
    set_aside: SetAside | None = None
    labels: tuple[Label, ...] = ()
    effects: tuple[TurnEffect, ...] = ()
    intentions: tuple[Intention, ...] = ()
    extras: dict[str, object] = field(default_factory=dict, compare=False)

    extra_posts: tuple[Post, ...] = ()
    acceptance_id: str | None = None
    acceptance_epoch: int | None = None
    acceptance_digest: str | None = None

    @property
    def posts(self) -> tuple[Post, ...]:
        return (() if self.post is None else (self.post,)) + self.extra_posts

    def contribution_id(self, index: int) -> str:
        return contribution_id(self.turn_id, index)

    def __post_init__(self) -> None:
        if type(self.chat) is not ChatRef:
            raise _fail("invalid_field", "a turn's chat is a ChatRef")
        object.__setattr__(self, "extra_posts", tuple(self.extra_posts))
        if len(self.extra_posts) > 15 or any(type(p) is not Post for p in self.extra_posts):
            raise _fail("invalid_field", "at most sixteen valid posts per turn")
        if self.extra_posts and self.post is None:
            raise _fail("invalid_field", "additional posts require a primary post")
        if any(p.actor.campaign_id != self.chat.campaign_id for p in self.extra_posts):
            raise _fail("invalid_field", "additional posts belong to this campaign")
        if self.extra_posts and (self.labels or self.effects):
            raise _fail("combined_adjudication_unsupported", "combined turns cannot carry adjudication or effects")
        if self.acceptance_id is not None:
            _text(self.acceptance_id, "acceptance_id", limit=100)
            if (type(self.acceptance_epoch) is not int or self.acceptance_epoch < 0
                    or type(self.acceptance_digest) is not str or len(self.acceptance_digest) != 64):
                raise _fail("invalid_field", "acceptance requires epoch and digest")
        elif self.acceptance_epoch is not None or self.acceptance_digest is not None:
            raise _fail("invalid_field", "acceptance metadata requires an id")
        turn_id(self.chat, self.number, self.device)
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
        if self.follows is not None:
            parent = parse_turn_id(self.follows) if type(self.follows) is str else None
            if parent is None or self.number == 1 or parent[:2] != (self.chat.chat_id, self.number - 1):
                raise _fail("invalid_field", "a turn follows a turn of its chat numbered one less")
        if self.set_aside is not None:
            chosen = parse_turn_id(self.set_aside.chosen) if type(self.set_aside) is SetAside else None
            if chosen is None or chosen[:2] != (self.chat.chat_id, self.number) or chosen[2] == self.device:
                raise _fail("invalid_field", "a set-aside turn names another turn of its number to go on from")
        object.__setattr__(self, "labels", tuple(self.labels))
        if not all(type(label) is Label for label in self.labels):
            raise _fail("invalid_field", "a turn's labellings are Label values")
        if self.labels and (self.post is None or self.post.kind != "action"):
            raise _fail("invalid_field", "only a turn whose post is an action is labelled")
        if [label.label for label in self.labels] != list(range(1, len(self.labels) + 1)):
            raise _fail("invalid_field", "labellings are numbered 1, 2, ... in order")
        for state in ("running", "framed"):
            if sum(label.state == state for label in self.labels) > 1:
                raise _fail("invalid_field", f"a turn holds at most one {state} labelling")
        for label in self.labels:
            planned = self.label_turn(label.label)
            if any(a.call_id.campaign_id != self.chat.campaign_id or a.call_id.turn_id != planned
                   for a in label.attempts()):
                raise _fail("invalid_field", "a labelling's calls were planned under <turn_id>.l<label>")
        object.__setattr__(self, "effects", tuple(self.effects))
        if not all(type(effect) is TurnEffect for effect in self.effects):
            raise _fail("invalid_field", "a turn's effects are TurnEffect values")
        if [effect.number for effect in self.effects] != list(range(1, len(self.effects) + 1)):
            raise _fail("invalid_field", "effects are numbered 1, 2, ... in order")
        if sum(effect.live for effect in self.effects) > 1:
            raise _fail("invalid_field", "a turn collects keys for at most one effect at a time")
        if sum(effect.state in ("accepting", "accepted") for effect in self.effects) > 1:
            raise _fail("invalid_field", "a turn has at most one accepted effect (021)")
        for effect in self.effects:  # the effect of this turn, at this turn's moment
            moment = effect.proposal.moment
            if effect.proposal.chat != self.chat or (moment.scene_id, moment.turn_id) != (self.chat.chat_id,
                                                                                        self.turn_id):
                raise _fail("invalid_field", "a turn's effect is proposed for that turn of its chat")
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
        intentions = tuple(self.intentions)
        if not all(type(intention) is Intention for intention in intentions):
            raise _fail("invalid_field", "a turn's intentions are Intention values")
        order = [(intention.take, str(intention.actor)) for intention in intentions]
        if order != sorted(set(order)):
            raise _fail("invalid_field", "a turn holds one intention per take and actor, in order")
        for intention in intentions:  # 039: a choice is for the one scene its take was generated for
            take = self.takes[intention.take - 1] if intention.take <= len(self.takes) else None
            # with a stage, the scene is who was on it (041): an absent cast member is never Present to a choice
            if take is None or take.origin != "generated" or intention.scene != frozenset(take.present):
                raise _fail("invalid_field", "an intention is for a generated take of this turn, and everyone present")
        object.__setattr__(self, "intentions", intentions)

    @property
    def turn_id(self) -> str:
        return turn_id(self.chat, self.number, self.device)

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

    def label_turn(self, label: int) -> str:
        """The id a labelling's call is planned and billed under: never a take's (``<turn_id>.<take>``)."""
        return f"{self.turn_id}.l{label}"

    def with_label(self, label: Label) -> TurnRecord:
        """The turn with ``label`` added, or replacing the labelling of the same number."""
        labels = [existing for existing in self.labels if existing.label != label.label]
        labels.append(label)
        return replace(self, labels=tuple(sorted(labels, key=lambda each: each.label)))

    @property
    def framed(self) -> Label | None:
        return next((label for label in self.labels if label.state == "framed"), None)

    def with_effect(self, effect: TurnEffect) -> TurnRecord:
        """The turn with ``effect`` added, or replacing the effect of the same number."""
        effects = [existing for existing in self.effects if existing.number != effect.number]
        effects.append(effect)
        return replace(self, effects=tuple(sorted(effects, key=lambda each: each.number)))

    def effect(self, number: int) -> TurnEffect | None:
        return next((effect for effect in self.effects if effect.number == number), None)

    def with_intentions(self, take: int, intentions: Iterable[Intention]) -> TurnRecord:
        """The turn with ``take``'s intentions replaced by ``intentions`` (each for that take)."""
        found = tuple(intentions)
        if any(type(intention) is not Intention or intention.take != take for intention in found):
            raise _fail("invalid_field", f"intentions for take {take} are that take's Intention values")
        kept = [intention for intention in self.intentions if intention.take != take]
        return replace(self, intentions=tuple(sorted((*kept, *found), key=lambda i: (i.take, str(i.actor)))))

    def intentions_of(self, take: int) -> tuple[Intention, ...]:
        return tuple(intention for intention in self.intentions if intention.take == take)

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
    "Against", "CALL_STATES", "CallRecord", "EFFECT_STATES", "INTENTION_OUTCOMES", "Intention", "IntentionOption",
    "LABEL_STATES",
    "LINE_STATES", "Label", "MAX_CHAT_ID", "MAX_EFFECTS", "MAX_LABELS",
    "MAX_TAGGED_CHAT_ID", "MAX_TAKES", "ORIGINS",
    "POST_KINDS", "Post",
    "SceneLine", "SceneRefused", "SetAside", "TAKE_STATES", "Take", "TokenUsage", "TurnEffect", "TurnRecord",
    "MAX_WAIVERS", "Waiver", "due_change_id", "parse_turn_id", "turn_id",
    "usage_totals",
]
