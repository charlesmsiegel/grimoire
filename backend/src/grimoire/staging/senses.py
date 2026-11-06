"""Questions to a stage (Sprint 041): where is it, can this actor reach it, see it, hear that speaker.

Every answer is director-side: the stage is the director's truth about the scene, so an answer is for the
runner, the referee and the director, never an actor's prompt. What an actor knows of where things are
comes only from its own 012 evidence, such as the perceptions ``perceptions`` builds.

Rules of this slice (room scale, no distances):

- **Reach.** Only an actor standing in an area acts. It reaches a non-actor in its own area that no other
  actor holds at any depth, and anything in its own custody.
- **Sight.** An actor on stage sees what is in its own area, or in an area joined to it by an open way. A
  closed or locked way blocks sight. Anything inside or on a thing (held by a non-actor) is out of sight,
  and so is everything to an actor who is inside a thing.
- **Hearing** (032's channels). ``speech`` carries through an open way to the next area and no further; a
  shut way stops it. ``whisper`` reaches only the same area. ``telepathy`` ignores ways and walls: any actor
  on stage. Who may send telepathically stays 032's (and later 025's) question.

``assumed`` in an answer lists the facts it rests on that are only assumed, as ``placement:<ref>`` and
``way:<way_id>``, so a caller can ask rather than invent.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from collections.abc import Mapping

from ..comms import CHANNELS, Message, Presence, Transmission, transmit
from ..core import Event, InstanceRef, Perception, Report
from ..party import SceneCast
from ..core.identity import SLOT_MAX, validate_segment
from ..evidence import TEXT_LIMIT
from .changes import reach_refusal
from .model import Stage, _label, checked, fail

#: An event id leaves room for the ``.w-<16 hex>`` suffix of its witnesses' perception ids.
EVENT_ID_MAX = SLOT_MAX - 19
#: What ``where`` answers.
WHEREABOUTS = ("in_area", "held", "off_stage", "untracked")


@dataclass(frozen=True, slots=True)
class Answer:
    """A yes or no, its machine reason, and the assumed facts it rests on (sorted)."""

    ok: bool
    reason: str
    assumed: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Whereabouts:
    """Where an individual is. ``area`` is the area its custody chain ends in (``None`` off stage);
    ``holder`` its direct holder; ``absent`` the off-stage actor it left with (itself, for that actor)."""

    status: str
    area: str | None = None
    holder: InstanceRef | None = None
    absent: InstanceRef | None = None
    assumed: tuple[str, ...] = ()


def _stage(stage: object) -> Stage:
    if type(stage) is not Stage:
        fail("invalid_field")
    return stage


def _ref(stage: Stage, value: object) -> InstanceRef:
    if type(value) is not InstanceRef:
        fail("invalid_field")
    if value.campaign_id != stage.campaign_id:
        fail("campaign_mismatch")
    return value


def _assumed_chain(stage: Stage, who: InstanceRef) -> list[str]:
    """The assumed placements of ``who`` and of every holder above it."""
    found = []
    current = stage.placement(who)
    while current is not None:
        if current.basis == "assumed":
            found.append(f"placement:{current.who}")
        current = stage.placement(current.holder) if current.holder is not None else None
    return found


def _answer(ok: bool, reason: str, *assumed: list[str]) -> Answer:
    return Answer(ok, reason, tuple(sorted({fact for group in assumed for fact in group})))


def where(stage: Stage, who: InstanceRef) -> Whereabouts:
    """Where ``who`` is: ``in_area``, ``held``, ``off_stage`` (an absent actor, or carried by one) or
    ``untracked`` (the stage holds nothing about it)."""
    stage = _stage(stage)
    who = _ref(stage, who)
    if stage.absence(who) is not None:
        return Whereabouts("off_stage", absent=who)
    placement = stage.placement(who)
    if placement is None:
        return Whereabouts("untracked")
    root = stage.root(who)
    assumed = tuple(sorted(_assumed_chain(stage, who)))
    if root.absent is not None:
        return Whereabouts("off_stage", holder=placement.holder, absent=root.absent, assumed=assumed)
    if placement.holder is not None:
        return Whereabouts("held", area=root.area, holder=placement.holder, assumed=assumed)
    return Whereabouts("in_area", area=root.area, assumed=assumed)


def reach(stage: Stage, who: InstanceRef, thing: InstanceRef) -> Answer:
    """Whether ``who`` can get ``thing`` into hand now: ``within_reach`` or ``in_hand`` (already in their
    custody), or why not (``not_actor``, ``not_on_stage``, ``restrained``, ``is_self``, ``is_actor``,
    ``untracked``, ``thing_off_stage``, ``held_by_other``, ``elsewhere``). The same rule ``take`` applies."""
    stage = _stage(stage)
    who, thing = _ref(stage, who), _ref(stage, thing)
    reason = reach_refusal(stage, who, thing)
    if reason is not None:
        return _answer(False, reason)
    assumed = (_assumed_chain(stage, who), _assumed_chain(stage, thing))
    if who in stage.root(thing).holders:
        return _answer(True, "in_hand", *assumed)
    return _answer(True, "within_reach", *assumed)


def _sensing(stage: Stage, actor: InstanceRef) -> tuple[str | None, bool]:
    """(the area an on-stage actor is in, whether it is inside a thing). Area ``None``: off stage."""
    root = stage.root(actor)
    if root.absent is not None:
        return None, False
    return root.area, any(not stage.is_actor(holder) for holder in root.holders)


def _between(stage: Stage, first: str, second: str) -> tuple[str, list[str]]:
    """How two different areas are joined: ``open`` (an open way), ``shut`` (only shut ways), ``none``.
    With the way facts the answer rests on."""
    ways = stage.ways_between(first, second)
    opened = [way for way in ways if way.state == "open"]
    if opened:
        return "open", [f"way:{way.way_id}" for way in opened if way.basis == "assumed"]
    if ways:
        return "shut", [f"way:{way.way_id}" for way in ways if way.basis == "assumed"]
    return "none", []


def sees(stage: Stage, viewer: InstanceRef, target: InstanceRef) -> Answer:
    """Whether actor ``viewer`` can see ``target`` now: ``same_area``, ``through_open_way`` or ``is_self``;
    or ``not_actor``, ``viewer_off_stage``, ``viewer_contained``, ``untracked``, ``target_off_stage``,
    ``contained`` (inside or on a thing), ``door_shut`` or ``elsewhere``."""
    stage = _stage(stage)
    viewer, target = _ref(stage, viewer), _ref(stage, target)
    if not stage.is_actor(viewer):
        return _answer(False, "not_actor")
    area, inside = _sensing(stage, viewer)
    if area is None:
        return _answer(False, "viewer_off_stage")
    if target == viewer:
        return _answer(True, "is_self")
    if inside:
        return _answer(False, "viewer_contained")
    if stage.placement(target) is None and stage.absence(target) is None:
        return _answer(False, "untracked")
    target_area, target_inside = _sensing(stage, target)
    if target_area is None:
        return _answer(False, "target_off_stage")
    if target_inside:
        return _answer(False, "contained")
    assumed = (_assumed_chain(stage, viewer), _assumed_chain(stage, target))
    if target_area == area:
        return _answer(True, "same_area", *assumed)
    joined, ways = _between(stage, area, target_area)
    if joined == "open":
        return _answer(True, "through_open_way", *assumed, ways)
    return _answer(False, "door_shut" if joined == "shut" else "elsewhere", *assumed, ways)


def hears(stage: Stage, listener: InstanceRef, speaker: InstanceRef, channel: str) -> Answer:
    """Whether actor ``listener`` physically receives what actor ``speaker`` says on ``channel`` now.

    Yes: ``same_area``, ``through_open_way`` (speech only), ``no_barrier`` (telepathy) or ``is_self``.
    No: ``unknown_channel``, ``not_actor``, ``speaker_off_stage``, ``listener_off_stage``, ``door_shut``,
    ``out_of_earshot`` (a whisper through an open way) or ``elsewhere``.
    """
    stage = _stage(stage)
    listener, speaker = _ref(stage, listener), _ref(stage, speaker)
    if channel not in CHANNELS or type(channel) is not str:
        return _answer(False, "unknown_channel")
    if not stage.is_actor(listener) or not stage.is_actor(speaker):
        return _answer(False, "not_actor")
    speaker_area, _ = _sensing(stage, speaker)
    if speaker_area is None:
        return _answer(False, "speaker_off_stage")
    listener_area, _ = _sensing(stage, listener)
    if listener_area is None:
        return _answer(False, "listener_off_stage")
    if listener == speaker:
        return _answer(True, "is_self")
    assumed = (_assumed_chain(stage, listener), _assumed_chain(stage, speaker))
    if channel == "telepathy":
        return _answer(True, "no_barrier", *assumed)
    if listener_area == speaker_area:
        return _answer(True, "same_area", *assumed)
    joined, ways = _between(stage, listener_area, speaker_area)
    if joined == "open":
        if channel == "speech":
            return _answer(True, "through_open_way", *assumed, ways)
        return _answer(False, "out_of_earshot", *assumed, ways)
    return _answer(False, "door_shut" if joined == "shut" else "elsewhere", *assumed, ways)


def _listeners(stage: Stage, speaker: InstanceRef, channel: str) -> list[tuple[InstanceRef, Answer]]:
    """Every other actor's hearing answer for ``speaker`` on ``channel``; the speaker must be on stage."""
    stage = _stage(stage)
    speaker = _ref(stage, speaker)
    if channel not in CHANNELS or type(channel) is not str:
        fail("unknown_channel")
    if not stage.is_actor(speaker):
        fail("not_actor")
    if _sensing(stage, speaker)[0] is None:
        fail("not_on_stage")
    return [(actor, hears(stage, actor, speaker, channel)) for actor in stage.actors if actor != speaker]


def audience(stage: Stage, speaker: InstanceRef, channel: str) -> tuple[InstanceRef, ...]:
    """The other actors who physically receive ``speaker`` on ``channel`` now, sorted by reference text.

    ``speaker`` must be an actor on stage (``not_actor``, ``not_on_stage``); an unknown channel is
    ``unknown_channel``.
    """
    return tuple(actor for actor, answer in _listeners(stage, speaker, channel) if answer.ok)


def _no_guess(answers, allow_assumed: object) -> None:
    """Records made from these answers become evidence: refuse ones that rest on assumed staging."""
    if type(allow_assumed) is not bool:
        fail("invalid_field")
    if not allow_assumed and any(answer.assumed for answer in answers):
        fail("rests_on_assumption")


def _current(stage: Stage, sequence: object, scene_id: object) -> None:
    """The stage must be the scene as it stands at ``sequence`` (``Stage.at`` says nothing changed)."""
    if type(sequence) is not int or sequence < 0:
        fail("invalid_field")
    if stage.as_of != sequence:
        fail("stale_stage")
    if scene_id is not None and scene_id != stage.scene_id:
        fail("scene_mismatch")


def presence(stage: Stage, message: Message, *, telepaths: tuple[InstanceRef, ...] = (),
             allow_assumed: bool = False) -> Presence:
    """A 032 ``Presence`` for ``message``: its sender and everyone who physically receives the sender on
    the message's own channel. ``transmit`` over it delivers only within earshot: a whisper to an actor
    behind a shut door is refused (``addressee_absent``), and speech is overheard only where it carries.

    Built from the message, never from a bare channel, so a presence made for speech cannot carry a
    whisper through an open door. Use it only for this message; ``deliver`` does both steps.
    ``telepaths`` (who may send telepathically; 032's input, later 025's) is kept for those present.
    A presence resting on assumed staging is ``rests_on_assumption`` unless ``allow_assumed``: it decides
    who receives evidence, which is never invented.
    """
    stage = _stage(stage)
    if type(message) is not Message:
        fail("invalid_field")
    if isinstance(telepaths, (str, bytes)) or not isinstance(telepaths, (tuple, list, frozenset, set)) \
            or any(type(ref) is not InstanceRef for ref in telepaths):
        fail("invalid_field")
    listeners = _listeners(stage, message.sender, message.channel)
    _no_guess([answer for _, answer in listeners] + [Answer(True, "", tuple(_assumed_chain(stage, message.sender)))],
              allow_assumed)
    present = (message.sender, *(actor for actor, answer in listeners if answer.ok))
    return Presence(stage.campaign_id, stage.scene_id, present,
                    telepaths=tuple(ref for ref in dict.fromkeys(telepaths) if ref in present))


def deliver(stage: Stage, message: Message, *, sequence: int, provenance: str, turn_id: str | None = None,
            telepaths: tuple[InstanceRef, ...] = (), allow_assumed: bool = False) -> Transmission:
    """032's ``transmit`` of ``message`` within earshot on this stage, at the stage's own ``sequence``.

    One rule is added to 032's: speech to the room is addressed only to the speaker's own area. A listener
    who hears it through an open way overhears it (an unaddressed ``overheard`` report with no ``sent_at``),
    so it never enters the speaker's ``sent``: the speaker did not knowingly tell them. A named addressee
    in the next area is still told. 032's refusals are raised as ``TransmissionError``.
    """
    stage = _stage(stage)
    if type(message) is not Message:
        fail("invalid_field")
    _current(stage, sequence, None)
    sent = transmit(message, presence(stage, message, telepaths=telepaths, allow_assumed=allow_assumed),
                    sequence=sequence, provenance=provenance, turn_id=turn_id)
    if message.channel != "speech" or message.addressees:
        return sent
    area = _sensing(stage, message.sender)[0]
    reports = tuple(
        report if _sensing(stage, report.holder)[0] == area else Report(
            report.campaign_id, report.evidence_id, report.holder, report.proposition_id, "overheard",
            report.moment, report.provenance, confirmed=True, sender=report.sender,
            attributed_to=report.attributed_to)
        for report in sent.reports)
    return Transmission(sent.message, sent.proposition, reports)


def _witness_answers(stage: Stage, area: str) -> list[tuple[InstanceRef, Answer]]:
    stage = _stage(stage)
    if type(area) is not str or stage.area(area) is None:
        fail("unknown_area")
    answers = []
    for actor in stage.actors:
        actor_area, inside = _sensing(stage, actor)
        if actor_area is None or inside:
            continue
        assumed = _assumed_chain(stage, actor)
        if actor_area == area:
            answers.append((actor, _answer(True, "same_area", assumed)))
            continue
        joined, ways = _between(stage, actor_area, area)
        answers.append((actor, _answer(joined == "open", "through_open_way" if joined == "open" else
                                       ("door_shut" if joined == "shut" else "elsewhere"), assumed, ways)))
    return answers


def witnesses(stage: Stage, area: str) -> tuple[InstanceRef, ...]:
    """The actors who can see what happens openly in ``area`` now: on stage, not inside a thing, and in
    ``area`` or in an area joined to it by an open way. Sorted by reference text."""
    return tuple(actor for actor, answer in _witness_answers(stage, area) if answer.ok)


def perception_id(event_id: str, holder: InstanceRef) -> str:
    """The id of ``holder``'s perception of an event: neutral, and silent about other witnesses."""
    return f"{event_id}.w-{hashlib.sha256(str(holder).encode('utf-8')).hexdigest()[:16]}"


def perceptions(stage: Stage, event: Event, *, area: str, percept: str, provenance: str,
                channel: str = "witnessed", allow_assumed: bool = False) -> tuple[Perception, ...]:
    """012 perceptions of ``event``, which happened openly in ``area``: one per witness, sorted by holder.

    ``percept`` is sensory register only (012's rule). The stage must be the scene at the event's own
    sequence (``stale_stage`` otherwise; ``Stage.at`` moves a stage forward when nothing changed), and in
    the event's scene when the event names one (``scene_mismatch``). Witnesses resting on assumed staging
    are ``rests_on_assumption`` unless ``allow_assumed``. 020 stores the result unchanged. An actor who had
    left, or stood behind a shut door, gets nothing, and so holds no evidence of the event.
    """
    stage = _stage(stage)
    if type(event) is not Event:
        fail("invalid_field")
    if event.campaign_id != stage.campaign_id:
        fail("campaign_mismatch")

    def shapes() -> None:
        _label(provenance)
        if type(percept) is not str or not percept.strip() or len(percept) > TEXT_LIMIT:
            raise ValueError("percept must be nonempty text of at most 20 000 characters")
        percept.encode("utf-8")
        validate_segment(channel, "channel")
    checked(shapes)
    if len(event.event_id) > EVENT_ID_MAX:
        fail("too_long")
    _current(stage, event.moment.sequence, event.moment.scene_id)
    answers = _witness_answers(stage, area)
    _no_guess([answer for _, answer in answers], allow_assumed)
    return tuple(
        Perception(stage.campaign_id, perception_id(event.event_id, holder), holder, event.event_id,
                   channel, percept, event.moment, provenance, confirmed=True)
        for holder, answer in answers if answer.ok)


def custody_divergence(stage: Stage, holders: Mapping[InstanceRef, InstanceRef]) -> tuple[InstanceRef, ...]:
    """The unique items whose staging disagrees with their canonical holder, sorted by reference text.

    ``holders`` maps each 034 unique item to its possession record's holder (034 is the authority for
    that). An item the stage tracks disagrees when its stage holder is not that holder, where a canonical
    holder the stage does not track (a place, a chest elsewhere) means resting in an area. The stage is a
    scene projection: a runner checks this when it builds a stage and before it proposes a 034 move.
    """
    stage = _stage(stage)
    if not isinstance(holders, Mapping):
        fail("invalid_field")
    diverging = []
    for item, holder in holders.items():
        _ref(stage, item)
        _ref(stage, holder)
        placement = stage.placement(item)
        if placement is None:
            continue
        tracked = stage.placement(holder) is not None or stage.absence(holder) is not None
        if placement.holder != (holder if tracked else None):
            diverging.append(item)
    return tuple(sorted(diverging, key=str))


def check_cast(stage: Stage, cast: SceneCast) -> None:
    """Refuse a stage whose actors are not exactly 033's staged cast of the same campaign (``cast_mismatch``).

    033's cast is who is present in the scene; the stage's absences are who of them has physically left.
    So the actors physically present are ``stage.on_stage()``, and a 032 presence comes from the stage.
    """
    stage = _stage(stage)
    if type(cast) is not SceneCast:
        fail("invalid_field")
    if cast.campaign_id != stage.campaign_id or set(cast.present) != set(stage.actors):
        fail("cast_mismatch")


__all__ = [
    "EVENT_ID_MAX", "WHEREABOUTS", "Answer", "Whereabouts", "audience", "check_cast", "custody_divergence",
    "deliver", "hears", "perception_id", "perceptions", "presence", "reach", "sees", "where", "witnesses",
]
