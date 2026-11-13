"""Explicit capture decisions (050). Values are proposals, never authority or actor memory."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from collections.abc import Mapping

from grimoire.core.identity import ChatRef, InstanceRef, validate_segment
from grimoire.core.knowledge import Moment
from grimoire.effects import ActedLine, EffectHoldings, EffectProposal, Op
from grimoire.holdings import Line

METADATA = 'capture_v1'


class CaptureRefused(ValueError):
    """A capture cannot proceed; no state was written. Reasons contain no authored text."""
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class CaptureRequest:
    attempt_id: str
    chat: ChatRef
    at: Moment
    trainer: InstanceRef
    creature: InstanceRef
    outcome: str
    device: str | None
    capacity: int
    placement: str = 'auto'
    trainer_backing: str | None = None
    creature_backing: str | None = None

    def __post_init__(self):
        try:
            validate_segment(self.attempt_id, 'attempt_id')
            if type(self.chat) is not ChatRef or type(self.at) is not Moment:
                raise ValueError()
            validate_segment(self.at.scene_id, 'scene_id')
            validate_segment(self.at.turn_id, 'turn_id')
            if type(self.trainer) is not InstanceRef or type(self.creature) is not InstanceRef:
                raise ValueError()
            if self.creature.kind != 'creature' or self.trainer == self.creature:
                raise ValueError()
            if {self.trainer.campaign_id, self.creature.campaign_id} != {self.chat.campaign_id}:
                raise ValueError()
            if type(self.capacity) is not int or self.capacity < 0:
                raise ValueError()
            if self.outcome not in ('success', 'failed', 'declined') or type(self.outcome) is not str:
                raise ValueError()
            if self.placement not in ('auto', 'party', 'reserve') or type(self.placement) is not str:
                raise ValueError()
            for value in (self.device, self.trainer_backing, self.creature_backing):
                if value is not None:
                    validate_segment(value, 'capture field')
        except (ValueError, TypeError):
            raise CaptureRefused('invalid_request') from None

    def payload(self) -> dict:
        return dict(attempt_id=self.attempt_id, chat=str(self.chat), sequence=self.at.sequence,
                    scene=self.at.scene_id, turn=self.at.turn_id, trainer=str(self.trainer),
                    creature=str(self.creature), outcome=self.outcome, device=self.device,
                    capacity=self.capacity, placement=self.placement, trainer_backing=self.trainer_backing,
                    creature_backing=self.creature_backing)

    @classmethod
    def from_payload(cls, value: object) -> CaptureRequest:
        if not isinstance(value, Mapping):
            raise CaptureRefused('invalid_capture_metadata')
        try:
            result = cls(value['attempt_id'], ChatRef.parse(value['chat']),
                         Moment(value['sequence'], value['scene'], value['turn']),
                         InstanceRef.parse(value['trainer']), InstanceRef.parse(value['creature']),
                         value['outcome'], value['device'], value['capacity'], value['placement'],
                         value['trainer_backing'], value['creature_backing'])
            if result.payload() != dict(value):
                raise ValueError()
            return result
        except (ValueError, TypeError, KeyError):
            raise CaptureRefused('invalid_capture_metadata') from None


def metadata(request: CaptureRequest, placement: str | None) -> dict:
    return {'request': request.payload(), 'placement': placement}


def event_id(request: CaptureRequest, placement: str | None) -> str:
    """Bind fields omitted by generic effect fingerprints to every orphan's provenance token."""
    raw = json.dumps(metadata(request, placement), sort_keys=True, separators=(',', ':'), ensure_ascii=True)
    return 'c50-' + hashlib.sha256(raw.encode('ascii')).hexdigest()


def check_event_id(value: str, request: CaptureRequest, placement: str | None) -> None:
    if re.fullmatch(re.escape(event_id(request, placement)) + r'(?:-[0-9a-f]{8})?', value) is None:
        raise CaptureRefused('capture_identity_mismatch')


def proposal_for(request: CaptureRequest, placement: str | None) -> EffectProposal | None:
    """A successful association is voluntary for both endpoints; neither inherits control of the other."""
    if request.outcome == 'declined' or (request.outcome == 'failed' and request.device is None):
        if placement is not None:
            raise CaptureRefused('invalid_placement')
        return None
    ops = ()
    if request.outcome == 'success':
        if placement not in ('party', 'reserve'):
            raise CaptureRefused('invalid_placement')
        ops = (Op('begin', 'trained_by', request.creature, request.trainer,
                  request.creature, request.creature_backing),
               Op('begin', 'in_party' if placement == 'party' else 'in_reserve', request.creature,
                  request.trainer, request.trainer, request.trainer_backing))
    elif placement is not None:
        raise CaptureRefused('invalid_placement')
    holdings = None if request.device is None else EffectHoldings((ActedLine(
        Line.consume(request.trainer, request.device, 1), request.trainer, request.trainer_backing),))
    return EffectProposal(request.chat.campaign_id, request.chat, request.at,
                          f'Capture {request.outcome}: {request.creature}.', ops, 'director',
                          attempt_id=request.attempt_id if holdings is not None else None, holdings=holdings)


@dataclass(frozen=True)
class CapturePreview:
    request: CaptureRequest
    placement: str | None
    proposal: EffectProposal | None
    endpoint_pins: tuple = ()
    relation_pins: tuple = ()

    def to_payload(self) -> dict:
        """Retain before commit for orphan recovery. This is a proposal, never a second ledger.

        Review pins intentionally do not survive this transport: a restored preview may be
        adopted (its orphan provenance authenticates its identity), but a new acceptance
        requires a freshly reviewed preview with current pins.
        """
        return metadata(self.request, self.placement)

    @classmethod
    def from_payload(cls, value: object) -> CapturePreview:
        if not isinstance(value, Mapping) or set(value) != {'request', 'placement'}:
            raise CaptureRefused('invalid_capture_metadata')
        request = CaptureRequest.from_payload(value['request'])
        placement = value['placement']
        return cls(request, placement, proposal_for(request, placement))


@dataclass(frozen=True)
class CaptureContext:
    """Director-only outcome context. It is not an observation, speaking cast or control assignment."""
    outcome: str
    trainer: InstanceRef
    creature: InstanceRef
    placement: str | None
