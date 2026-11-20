"""What an accepted effect leaves in 020: its 012 bridge event and who witnessed it (035 milestone 2, 041).

When a turn's effect is ``accepted`` in play (``play/accept.py``), the campaign's 020 ledger gets, in one
append, exactly once:

- the effect's 012 ``Event``, 021's ``as_knowledge_event`` of its accepted event (the only event 020 stores
  under an accepted id, so the director's events file holds no witness: 020 keeps events and evidence apart);
- one 012 ``Perception`` for each cast actor who saw it happen, judged by 041's ``perceptions`` on the chat's
  stage at the event's own sequence, as an open happening in the area of the turn's acting character, and only
  for a witness whose player holds a key on the effect (that player was shown its summary), both when it was
  accepted and now. An NPC witness gets none: its knowledge outlives a hand-over of the character to a player.

This is best effort and never a refusal: acceptance is 021's, and 021 never depended on 020. Every id is
deterministic (the bridge is the accepted event's id, a perception is 041's ``perception_id``), and "done" is
read from 020 itself (an event under that id, stored or quarantined), so a re-run never writes twice. Nothing
marks a bridge as pending: an accepted effect whose event 020 does not hold is pending, and ``settle`` writes
every pending one of the campaign, oldest first, right after an acceptance, on ``open`` and in
``recover_effects``; what stays pending is reported (``unbridged``), counted per chat, never named.
See docs/SCENES.md#what-an-accepted-effect-leaves-in-knowledge.

Fail closed, never on a guess, and never waiting for a stage: no stage (a chat played by the cast alone, or one
lost on a restart), a stage the runner refused, a stage that changed after the event's sequence (``Stage.at``
cannot go back), an acting character off stage or untracked, a private post (a whisper, telepathy, a withheld
action), an area that cannot be told, a witness resting on assumed staging, and an effect no player holds a key on
write the event with **no** perception. 020 never takes a backdated record, so a perception cannot be
added later: the event alone is final.
"""

from __future__ import annotations

from dataclasses import dataclass

from grimoire.core.identity import AcceptedRef, ChatRef
from grimoire.core import DIRECTOR
from grimoire.core.knowledge import Event, Perception
from grimoire.core.model import ControlAssignment
from grimoire.effects import AcceptedEvent, EffectCodecError, EffectError, accepted_from_payload, as_knowledge_event
from grimoire.evidencestore import EvidenceLibrary
from grimoire.play.scene import _latest, _refuse
from grimoire.scene import SceneRefused, TurnEffect, TurnRecord, turn_from_payload
from grimoire.staging import StagingRefused, perceptions, where
from grimoire.store import StaleWrite, StoreError

#: The channel of a witness's perception of an accepted effect (012's open vocabulary): it saw it happen.
CHANNEL = "witnessed"

#: 020 refusals that would hold for every later bridge too (the ledger, the accepted files, a gap in sequences
#: or a concurrent writer): the walk stops there, and the rest wait for the next ``settle``. Any other refusal is
#: this bridge's own (an actor or a chat gone), so later ones go on and it falls behind for good.
_WIDE = frozenset({"ledger_damaged", "accepted_unreadable", "sequence_jump", "knowledge_changed",
                   "knowledge_unavailable"})


@dataclass(frozen=True)
class Pending:
    """An accepted effect whose bridge 020 does not hold: its 021 event, its turn effect and the turn.

    ``behind`` is true once 020's high-water mark has passed the event's sequence: 020 refuses it as backdated
    for good (no backdating, EVIDENCE.md), so it is only counted. ``elsewhere`` is true for another device's
    acceptance: that device writes its knowledge, and it arrives by sync, so this library only counts it while it
    has not (a device retired before it could, a replaced or damaged identity), never writes it."""

    event: AcceptedEvent
    effect: TurnEffect
    record: TurnRecord
    behind: bool
    elsewhere: bool = False

    @property
    def chat(self) -> ChatRef:
        return self.record.chat


@dataclass(frozen=True)
class Left:
    """A bridge still not in 020 after ``settle``, and why (``backdated``, ``acceptance_unsettled``, ``elsewhere``
    for another device's acceptance whose knowledge has not arrived, or 020's,
    the store's or the effect's reason)."""

    pending: Pending
    reason: str


def _ledger(runner, campaign_id: str):
    """020's director read and its high-water mark, or a refusal (``knowledge_unavailable``)."""
    try:
        ledger = EvidenceLibrary(runner.store, runner.handle, owner=runner.owner).read(campaign_id,
                                                                                      requested_by=DIRECTOR)
    except (StoreError, ValueError) as error:
        raise _refuse("knowledge_unavailable", getattr(error, "reason", type(error).__name__)) from None
    return ledger, _latest(ledger.knowledge)


def controls_at(runner, campaign_id: str, position: int) -> tuple[ControlAssignment, ...]:
    """The campaign's control as the sovereignty ledger stood at ``position`` (replayed from its history)."""
    controllers: dict[str, str] = {}
    for event in runner.sovereignty.load(campaign_id).control_history:
        if event.seq > position:
            break
        if event.controller_id is None:
            controllers.pop(event.actor_id, None)
        else:
            controllers[event.actor_id] = event.controller_id
    return tuple(ControlAssignment(campaign_id, actor, controller) for actor, controller in sorted(controllers.items()))


def unbridged(runner, campaign_id: str) -> tuple[list[Pending], int, int | None]:
    """Every accepted effect of the campaign's turns whose 021 event is active in this library and whose bridge
    020 does not hold, oldest first (by sequence, then event id); 020's high-water mark; and the earliest sequence
    of an acceptance still cut short (``accepting``) anywhere in the campaign, or ``None``.

    Read raw: a turn or accepted file that cannot be read is skipped here (``open`` and acceptance already report
    and refuse over it). Only this device's acceptances are this library's to bridge (tagged with this device, or
    with none): 028 merges 021's accepted events and 020's knowledge alike, so another device's acceptance arrives
    with the knowledge that device wrote, judged on the stage it was played on. A bridge 020 quarantined (a damaged
    event file) counts as held: it is repaired, never written again.
    """
    ledger, high = _ledger(runner, campaign_id)
    try:
        mine = runner.scenes.device()
    except SceneRefused:  # a damaged identity: only untagged acceptances are known to be this library's
        mine = None
    held = {event.event_id for event in ledger.knowledge.events}
    held |= {item.name.partition(":")[2] for item in ledger.quarantined if item.name.startswith("event:")}
    try:
        accepted = runner.store.campaign_accepted(campaign_id).records
        turns = runner.store.campaign_turns(campaign_id).records
    except StoreError as error:
        raise _refuse("effects_unavailable", type(error).__name__) from None
    found, unsettled = [], None
    for ref, stored in turns.items():
        try:
            record = turn_from_payload(ref, stored.record.schema, stored.record.payload)
        except SceneRefused:
            continue
        for effect in record.effects:
            if effect.state == "accepting":
                sequence = effect.proposal.moment.sequence
                unsettled = sequence if unsettled is None else min(unsettled, sequence)
            if effect.state != "accepted" or effect.event_id is None or effect.event_id in held:
                continue
            elsewhere = effect.accepted_on is not None and effect.accepted_on != mine  # that device writes it
            address = AcceptedRef(campaign_id, effect.event_id)
            raw = accepted.get(address)
            if raw is None:
                continue
            try:
                event = accepted_from_payload(address, raw.record.schema, raw.record.payload)
            except EffectCodecError:
                continue
            if event.active and event.chat == record.chat:
                found.append(Pending(event, effect, record, event.moment.sequence < high, elsewhere))
    found.sort(key=lambda item: (item.event.moment.sequence, item.event.event_id))
    return found, high, unsettled


def records(runner, scene, pending: Pending) -> tuple[Event | Perception, ...] | str:
    """The bridge of ``pending``: its event, then each witness's perception; or why there is none (revoked since
    it was listed: an undone turn is never stored as fact). ``scene`` is its chat's, or ``None`` when that chat
    cannot be opened now, which leaves the event alone. Control at acceptance is replayed from the sovereignty
    ledger position the acceptance recorded (``controls_at``; control now when none was recorded)."""
    try:
        event = as_knowledge_event(pending.event)
    except EffectError as error:
        return error.reason
    if scene is None:
        return (event,)
    then = scene.controls
    if pending.effect.controls_at is not None:
        try:
            then = controls_at(runner, scene.chat.campaign_id, pending.effect.controls_at)
        except (StoreError, ValueError):  # control then cannot be told: nobody's perception
            return (event,)
    return (event, *witnessed(scene, pending.record, pending.effect, event, then))


def _controller(controls, actor) -> str | None:
    found = {item.controller_id for item in controls
             if item.campaign_id == actor.campaign_id and item.actor_id == actor.instance_id}
    return next(iter(found)) if len(found) == 1 else None


def witnessed(scene, record: TurnRecord, effect: TurnEffect, event: Event, then=None) -> tuple[Perception, ...]:
    """Each present cast actor's perception of ``event``, an open happening where the turn's acting character
    stands, on the chat's stage at the event's sequence; ``()`` whenever that cannot be told without a guess.

    The percept is the effect's summary, and only a player who holds a key on the effect was shown it (the hub's
    ``turn_view`` rule), so a perception goes only to a witness that player controls, both when the effect was
    accepted (``then``, by default control now) and now. A director-only effect (no endorser) leaves none, and
    neither does an NPC witness: the director may have written in the summary what no character there could
    see, and an NPC's evidence would reach whichever player is given that character later. A character handed
    over after its perception was written takes it to its new player, as a hand-over takes what that character
    was shown; covert acts, and NPC perspectives of play, are not modelled.

    With no stage (the cast alone decides, a world-less chat) nobody gets one: the cast says who is present, not
    who could see, and a stage lost on restart or refused reads the same way. The stage is the source's latest,
    its word that nothing physical changed since its own sequence, so it stands for the event's sequence only
    when it is not newer than the event. The percept is the event's summary: 021's neutral description of what
    the operations say, which the player who endorsed it was shown (012's sensory register; EFFECTS.md)."""
    keys = {who for _, who in effect.endorsers}
    stage = scene.stage
    sequence = event.moment.sequence
    if not keys or stage is None or stage.as_of > sequence:
        return ()
    try:
        at = stage.at(sequence)
        area = _area(at, record)
        if area is None:
            return ()
        found = perceptions(at, event, area=area, percept=event.summary, provenance=event.provenance,
                            channel=CHANNEL)
    except StagingRefused:  # rests on assumed staging, or the event cannot be judged on this stage: nobody
        return ()
    present = set(scene.on_stage)  # the stage's actors are the cast (check_cast); never anyone else
    then = scene.controls if then is None else then
    return tuple(item for item in found if item.holder in present and _controller(then, item.holder) in keys
                 and scene.controller(item.holder) in keys)  # a key is a human player's, never the director's


def _area(stage, record: TurnRecord) -> str | None:
    """Where the turn's effect happens: the area its post's character stands in (held or not), established, or,
    with no post, the stage's only area. ``None`` (no witness) for an actor off stage, untracked or placed only
    by assumption, for a turn with no post on a stage of several areas, and for a turn whose post was private (a
    whisper, telepathy or a withheld action is no open happening, whatever its effect says)."""
    post = record.post
    if post is None:
        return stage.areas[0].area_id if len(stage.areas) == 1 else None
    if post.private or post.actor not in stage.actors:
        return None
    found = where(stage, post.actor)
    if found.status not in ("in_area", "held") or found.assumed:
        return None
    return found.area


def _append(runner, pending: Pending, found: tuple) -> tuple[str | None, bool]:
    """One 020 append of ``found`` (records and one log batch, one transaction, so a crash leaves all of it or
    none): ``(None, _)`` when 020 holds the bridge now or already, else why not and whether it is ``_WIDE``. A
    ``duplicate_id`` on the event means another writer stored it first: done, never written twice."""
    library = EvidenceLibrary(runner.store, runner.handle, owner=runner.owner)
    name = f"event:{pending.event.event_id}"
    try:
        library.append(pending.chat.campaign_id, found, chat=pending.chat).commit()
    except StaleWrite:
        return "knowledge_changed", True
    except StoreError as error:  # possibly published in part (020's own damage report says so on the next read)
        return type(error).__name__, True
    except ValueError as error:  # 020's EvidenceWriteError, 012's KnowledgeError: refused, nothing written
        reason = getattr(error, "reason", type(error).__name__)
        if reason == "duplicate_id" and name in getattr(error, "records", ()):
            return None, False
        return reason, reason in _WIDE
    return None, False


def write(runner, scene, pending: Pending) -> tuple[str | None, bool]:
    """Append ``pending``'s bridge to 020 once: ``(None, False)`` when 020 holds it, else why not and whether the
    reason holds for every later bridge too. A witness 020 will not take (an actor gone) leaves the event alone."""
    found = records(runner, scene, pending)
    if isinstance(found, str):
        return found, False
    reason, wide = _append(runner, pending, found)
    if reason is not None and not wide and len(found) > 1:
        reason, wide = _append(runner, pending, found[:1])
    return reason, wide


def _scene_of(runner, chat: ChatRef, opened: dict):
    """``chat``'s scene as the runner opens it now, once per walk; ``None`` when it cannot be opened (its bridge
    is then the event alone: who saw it cannot be told)."""
    if chat not in opened:
        try:
            opened[chat] = runner._context(chat).scene
        except (SceneRefused, StoreError, ValueError):
            opened[chat] = None
    return opened[chat]


def settle(runner, scene) -> list[Left] | str:
    """Write every pending bridge of ``scene``'s campaign, oldest first, under the store lock; what stays pending
    and why, or why nothing could be listed. Never refuses: acceptance and recovery go on whatever 020 says.

    020 never takes a backdated record, so bridges go in sequence order, campaign-wide, each with its own chat's
    stage (``scene`` for its own chat, the runner's ``open`` of another). Stopped at an acceptance still cut short
    no later than it (once settled, that one's bridge would be behind for good: it waits for recovery, which
    blocks acceptance anyway) and by a reason that holds for every later bridge (``_WIDE``). A bridge refused for
    its own reason is passed: later ones go on, and it falls behind (``backdated``) rather than stall the rest."""
    with runner.store.lock:
        try:
            pending, high, unsettled = unbridged(runner, scene.chat.campaign_id)
        except SceneRefused as error:
            return error.reason
        left, stop, opened = [], None, {scene.chat: scene}
        for item in pending:
            sequence = item.event.moment.sequence
            if item.elsewhere:  # counted, never written here: its own device writes it, and it arrives by sync
                left.append(Left(item, "elsewhere"))
                continue
            if item.behind or sequence < high:
                left.append(Left(item, "backdated"))
                continue
            if stop is None and unsettled is not None and unsettled <= sequence:
                stop = "acceptance_unsettled"
            if stop is not None:
                left.append(Left(item, stop))
                continue
            reason, wide = write(runner, _scene_of(runner, item.chat, opened), item)
            if reason is None:
                high = max(high, sequence)
                continue
            left.append(Left(item, reason))
            if wide:
                stop = reason
        return left


def problems(runner, scene) -> list[tuple[str, str]]:
    """``open``'s report, after a ``settle``: one row for this chat's accepted effects whose bridge 020 still
    does not hold, ``("unbridged", "<count>:<reason>")``, with the first reason that may still clear
    (``backdated`` only when none may), or ``("unbridged", "unreadable:<reason>")``. It names no turn and no
    event: the hub shows problems to the table, and a director-only effect's existence is the director's."""
    left = settle(runner, scene)
    if isinstance(left, str):
        return [("unbridged", f"unreadable:{left}")]
    mine = [item for item in left if item.pending.chat == scene.chat]
    if not mine:
        return []
    clearing = [item.reason for item in mine if item.reason != "backdated"]
    return [("unbridged", f"{len(mine)}:{clearing[0] if clearing else 'backdated'}")]


__all__ = ["CHANNEL", "Left", "Pending", "controls_at", "problems", "records", "settle", "unbridged", "witnessed",
           "write"]
