"""What an accepted effect leaves in 020: its 012 bridge event and who witnessed it (035 milestone 2, 041).

When a turn's effect is ``accepted`` in play (``play/accept.py``), the campaign's 020 ledger gets, in one
append, exactly once:

- the effect's 012 ``Event``, 021's ``as_knowledge_event`` of its accepted event (the only event 020 stores
  under an accepted id, so the director's events file holds no witness: 020 keeps events and evidence apart);
- one 012 ``Perception`` for each cast actor who saw it happen, judged by 041's ``perceptions`` on the chat's
  stage at the event's own sequence, as an open happening in the area of the turn's acting character.

Every id is deterministic (the bridge is the accepted event's id, a perception is 041's ``perception_id``), and
"done" is read from 020 itself (an event under that id, stored or quarantined), so a re-run never writes twice.
Nothing marks a bridge as pending: an accepted effect whose event 020 does not hold is pending, and is written
by the next acceptance in its chat or by ``recover_effects`` (``bridge_chat``), oldest first; ``open`` reports it
(``unbridged``). See docs/SCENES.md#what-an-accepted-effect-leaves-in-knowledge.

Fail closed, never on a guess: no stage (a chat played by the cast alone), a stage that changed after the event's
sequence (``Stage.at`` cannot go back), an acting character off stage or untracked, a private post (a whisper,
telepathy, a withheld action), an area that cannot be told, or any witness resting on assumed staging writes the
event with **no** perception. A stage the runner
refused (``stage`` problem) writes nothing yet: the bridge waits until the scene is staged again.
"""

from __future__ import annotations

from dataclasses import dataclass

from grimoire.core import DIRECTOR
from grimoire.core.identity import AcceptedRef, ChatRef
from grimoire.core.knowledge import Event, Perception
from grimoire.effects import AcceptedEvent, EffectCodecError, EffectError, accepted_from_payload, as_knowledge_event
from grimoire.evidencestore import EvidenceLibrary
from grimoire.play.scene import _latest, _refuse
from grimoire.scene import SceneRefused, TurnRecord, turn_from_payload
from grimoire.staging import StagingRefused, perceptions, where
from grimoire.store import StaleWrite, StoreError

#: The channel of a witness's perception of an accepted effect (012's open vocabulary): it saw it happen.
CHANNEL = "witnessed"


@dataclass(frozen=True)
class Pending:
    """An accepted effect whose bridge 020 does not hold: its 021 event, and the turn that accepted it.

    ``behind`` is true once 020's high-water mark has passed the event's sequence: 020 refuses it as backdated
    for good (no backdating, EVIDENCE.md), so it is reported and blocks nothing."""

    event: AcceptedEvent
    record: TurnRecord
    behind: bool

    @property
    def chat(self) -> ChatRef:
        return self.record.chat

    @property
    def named(self) -> str:
        return f"{self.record.turn_id}:{self.event.event_id}"


def _ledger(runner, campaign_id: str):
    """020's director read and its high-water mark, or a refusal (``knowledge_unavailable``)."""
    try:
        ledger = EvidenceLibrary(runner.store, runner.handle, owner=runner.owner).read(campaign_id,
                                                                                      requested_by=DIRECTOR)
    except (StoreError, ValueError) as error:
        raise _refuse("knowledge_unavailable", getattr(error, "reason", type(error).__name__)) from None
    return ledger, _latest(ledger.knowledge)


def unbridged(runner, campaign_id: str) -> tuple[list[Pending], int, int | None]:
    """Every accepted effect of the campaign's turns whose 021 event is active in this library and whose bridge
    020 does not hold, oldest first (by sequence, then event id); 020's high-water mark; and the earliest sequence
    of an acceptance still cut short (``accepting``) anywhere in the campaign, or ``None``.

    Read raw: a turn or accepted file that cannot be read is skipped here (``open`` and acceptance already report
    and refuse over it). An effect another device accepted has no 021 event here, so it is never this library's
    to bridge. A bridge 020 quarantined (a damaged event file) counts as held: it is repaired, never written again.
    """
    ledger, high = _ledger(runner, campaign_id)
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
            address = AcceptedRef(campaign_id, effect.event_id)
            raw = accepted.get(address)
            if raw is None:
                continue
            try:
                event = accepted_from_payload(address, raw.record.schema, raw.record.payload)
            except EffectCodecError:
                continue
            if event.active and event.chat == record.chat:
                found.append(Pending(event, record, event.moment.sequence < high))
    found.sort(key=lambda item: (item.event.moment.sequence, item.event.event_id))
    return found, high, unsettled


def records(scene, pending: Pending) -> tuple[Event | Perception, ...] | str:
    """The bridge of ``pending`` (its event, then each witness's perception), or why it waits: the chat's stage
    was refused (the ``stage`` problem's detail), so who saw it cannot be told now. ``scene`` is its chat's."""
    try:
        event = as_knowledge_event(pending.event)
    except EffectError as error:  # revoked since it was listed: an undone turn is never stored as fact
        return error.reason
    for code, detail in scene.problems:
        if code == "stage":
            return detail
    return (event, *witnessed(scene, pending.record, event))


def witnessed(scene, record: TurnRecord, event: Event) -> tuple[Perception, ...]:
    """Each present cast actor's perception of ``event``, an open happening where the turn's acting character
    stands, on the chat's stage at the event's sequence; ``()`` whenever that cannot be told without a guess.

    With no stage (the cast alone decides, a world-less chat) nobody gets one: the cast says who is present, not
    who could see, and a stage lost on restart reads the same way. The stage is the source's latest, its word
    that nothing physical changed since its own sequence, so it stands for the event's sequence only when it is
    not newer than the event. The percept is the event's summary: 021's neutral director description of what the
    operations say, which the player who endorsed it was shown (012's sensory register; EFFECTS.md)."""
    stage = scene.stage
    sequence = event.moment.sequence
    if stage is None or stage.as_of > sequence:
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
    return tuple(item for item in found if item.holder in present)


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


def write(runner, scene, pending: Pending) -> str | None:
    """Append ``pending``'s bridge to 020 once; ``None`` when 020 holds it (now, or already), else why it waits.

    One 020 append (records and one log batch, one transaction), so a crash leaves all of it or none. A
    ``duplicate_id`` on the event means another writer stored it first: done, never written twice."""
    found = records(scene, pending)
    if isinstance(found, str):
        return found
    library = EvidenceLibrary(runner.store, runner.handle, owner=runner.owner)
    name = f"event:{pending.event.event_id}"
    try:
        library.append(scene.chat.campaign_id, found, chat=scene.chat).commit()
    except StaleWrite:
        return "knowledge_changed"
    except StoreError as error:  # possibly published in part (020's own damage report says so on the next read)
        return type(error).__name__
    except ValueError as error:  # 020's EvidenceWriteError, 012's KnowledgeError: refused, nothing written
        if getattr(error, "reason", None) == "duplicate_id" and name in getattr(error, "records", ()):
            return None
        return getattr(error, "reason", type(error).__name__)
    return None


def bridge_chat(runner, scene) -> int:
    """Write every pending bridge of ``scene``'s chat, oldest first, under the caller's store lock; 020's
    high-water mark after them.

    Bridges go to 020 in sequence order, campaign-wide, so none is ever left behind a later one (020 refuses a
    backdated record for good): the walk stops at another chat's pending bridge (``recovery_needed``: that
    chat's recovery or next acceptance writes it), at one no earlier than an acceptance still cut short anywhere
    (``recovery_needed``: once settled, that one's bridge would be behind it for good), and at one of this
    chat's that cannot be written now (``unbridged``, with 020's or the stage's reason). One that fell behind
    020's high-water mark is skipped."""
    pending, high, unsettled = unbridged(runner, scene.chat.campaign_id)
    for item in pending:
        if item.behind:
            continue
        if unsettled is not None and unsettled <= item.event.moment.sequence:
            raise _refuse("recovery_needed", "recover first: an acceptance cut short comes no later than "
                                             f"{item.named}, whose knowledge waits for it")
        if item.chat != scene.chat:
            later = sum(1 for other in pending if not other.behind and other.chat != scene.chat)
            raise _refuse("recovery_needed", f"recover first: {later} accepted effect(s) elsewhere in the campaign "
                                             "whose knowledge is not written yet")
        reason = write(runner, scene, item)
        if reason is not None:
            raise _refuse("unbridged", f"the knowledge of {item.named} cannot be written yet ({reason})"[:200])
        high = max(high, item.event.moment.sequence)
    return high


def problems(runner, scene) -> list[tuple[str, str]]:
    """``open``'s report: each of this chat's accepted effects whose bridge 020 does not hold, as ``unbridged``
    (``<turn id>:<event id>``, with ``:backdated`` when 020 can never take it), or why that cannot be read."""
    try:
        pending, _, _ = unbridged(runner, scene.chat.campaign_id)
    except SceneRefused as error:
        return [("unbridged", f"unreadable:{error.reason}")]
    return [("unbridged", item.named + (":backdated" if item.behind else "")) for item in pending
            if item.chat == scene.chat]


__all__ = ["CHANNEL", "Pending", "bridge_chat", "problems", "records", "unbridged", "witnessed", "write"]
