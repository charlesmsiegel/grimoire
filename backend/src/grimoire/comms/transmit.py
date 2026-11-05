"""Deliver one in-scene message to exactly its valid receivers, as 012 records.

Channel rules (first slice):

- ``speech`` to the room (no addressees) reaches everyone present but the sender, addressed.
  Speech to named addressees reaches them addressed (``told``); every other present actor
  overhears it (``overheard``, unaddressed), because speaking aloud is not private.
- ``whisper`` and ``telepathy`` reach their addressees only (``whispered``, ``telepathic``).
  A bystander receives nothing: no report, so no evidence and no ``sent`` entry about them.
- ``telepathy`` needs a sender the scene lists among its telepaths.

Every receiver is present, so receipt is certain in this slice: reports are ``confirmed`` and
attribute the message to its sender. Imperfect receipt, range, languages, barriers and
eavesdropping are later milestones. The receipt moment is the send moment.
"""

from __future__ import annotations

import hashlib

from ..core import InstanceRef, KnowledgeError, Moment, Proposition, Report
from .model import LABEL_LIMIT, MAX_RECEIVERS, Message, Presence, Transmission, TransmissionError, _fail, storable

_RECEIVED = {"speech": "told", "whisper": "whispered", "telepathy": "telepathic"}
_OVERHEARD = "overheard"


def evidence_id(message_id: str, holder: InstanceRef) -> str:
    """The id of ``holder``'s report of a message: neutral, and silent about other receivers."""
    return f"{message_id}.r-{hashlib.sha256(str(holder).encode('utf-8')).hexdigest()[:16]}"


def _label(value: object, required: bool) -> bool:
    if value is None:
        return not required
    return storable(value) and bool(value.strip()) and len(value) <= LABEL_LIMIT


def _receivers(message: Message, presence: Presence) -> tuple[tuple[InstanceRef, ...], tuple[InstanceRef, ...]]:
    """(addressed receivers, overhearing receivers), each sorted by reference text."""
    present = set(presence.present)
    if message.sender.campaign_id != presence.campaign_id or any(
            ref.campaign_id != presence.campaign_id for ref in message.addressees):
        _fail("campaign_mismatch")
    if message.sender not in present:
        _fail("sender_absent")
    if message.sender in message.addressees:
        _fail("self_addressed")
    if any(ref not in present for ref in message.addressees):
        _fail("addressee_absent")
    others = tuple(ref for ref in presence.present if ref != message.sender)
    if message.channel == "speech":
        if not message.addressees:
            if not others:
                _fail("no_audience")
            return others, ()
        return message.addressees, tuple(ref for ref in others if ref not in message.addressees)
    if not message.addressees:
        _fail("no_addressee")
    if message.channel == "telepathy" and message.sender not in presence.telepaths:
        _fail("not_telepathic")
    return message.addressees, ()


def _report(message: Message, holder: InstanceRef, moment: Moment, provenance: str,
            addressed: bool) -> Report:
    return Report(
        message.sender.campaign_id, evidence_id(message.message_id, holder), holder,
        message.message_id, _RECEIVED[message.channel] if addressed else _OVERHEARD, moment,
        provenance, confirmed=True, sender=message.sender, attributed_to=message.sender,
        addressed=addressed, sent_at=moment if addressed else None)


def transmit(message: Message, presence: Presence, *, sequence: int, provenance: str,
             turn_id: str | None = None) -> Transmission:
    """Deliver ``message`` in ``presence``'s scene at campaign ``sequence``.

    Returns the new proposition (id ``message.message_id``, the message's text, received at
    ``sequence`` in this scene and turn) and one report per receiver, sorted by holder. Refusals
    raise ``TransmissionError`` with a reason and no text, and deliver nothing. Who may speak
    for ``sender`` is the caller's authority check (003, 035): this only decides who receives.
    """
    if type(message) is not Message or type(presence) is not Presence:
        _fail("invalid_record")
    if type(sequence) is not int or sequence < 0 or not _label(provenance, True) or not _label(turn_id, False):
        _fail("invalid_record")
    addressed, overhearing = _receivers(message, presence)
    if len(addressed) + len(overhearing) > MAX_RECEIVERS:
        _fail("too_many_receivers")
    moment = Moment(sequence, presence.scene_id, turn_id)
    reason = None
    try:
        proposition = Proposition(presence.campaign_id, message.message_id, message.text, moment,
                                  provenance, about=message.about)
    except KnowledgeError as error:
        reason = error.reason
    if reason is not None:
        _fail(reason)
    reports = sorted(
        [_report(message, holder, moment, provenance, True) for holder in addressed]
        + [_report(message, holder, moment, provenance, False) for holder in overhearing],
        key=lambda report: str(report.holder))
    return Transmission(message, proposition, tuple(reports))


__all__ = ["TransmissionError", "evidence_id", "transmit"]
