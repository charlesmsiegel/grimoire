"""Capture as one accepted-effect transaction, reusing the native journal and party contracts."""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace

from grimoire.core import DIRECTOR
from grimoire.core.identity import AcceptedRef, paired_instances
from grimoire.core.json_value import JsonObject
from grimoire.effects import accepted_from_payload, accepted_to_payload
from grimoire.effectstore import EffectChange, EffectRecovery, TurnEffectsLibrary
from grimoire.graph import Relation
from grimoire.graph.codec import relation_from_payload
from grimoire.graph.index import require_director
from grimoire.library import LibraryPathError
from grimoire.party import MEMBERSHIP, PartyState
from grimoire.store import StaleWrite, StoreError

from .model import (METADATA, CaptureContext, CapturePreview, CaptureRefused, CaptureRequest,
                    check_event_id, event_id, metadata, proposal_for)


@dataclass(frozen=True)
class CaptureCommit:
    event: AcceptedRef | None
    etags: Mapping[str, str]


@dataclass
class CaptureChange:
    """A reviewed capture; effect owns every write and its commit-time checks."""
    decision: CapturePreview
    effect: EffectChange | None

    def preview(self) -> dict[str, bytes]:
        return {} if self.effect is None else self.effect.preview()

    def commit(self, approvals=None) -> CaptureCommit:
        if self.effect is None:
            return CaptureCommit(None, {})
        result = self.effect.commit(approvals)
        return CaptureCommit(result.ref, result.etags)

    def context(self, *, requested_by: str) -> CaptureContext:
        require_director(requested_by)
        r = self.decision.request
        return CaptureContext(r.outcome, r.trainer, r.creature, self.decision.placement)


def recorded(event) -> CapturePreview | None:
    value = event.extras.get(METADATA)
    if value is None:
        return None
    if not isinstance(value, Mapping) or set(value) != {'request', 'placement'}:
        raise CaptureRefused('invalid_capture_metadata')
    request = CaptureRequest.from_payload(value['request'])
    placement = value['placement']
    proposal = proposal_for(request, placement)
    check_event_id(event.event_id, request, placement)
    if proposal is None or proposal.fingerprint != event.fingerprint:
        raise CaptureRefused('invalid_capture_metadata')
    return CapturePreview(request, placement, proposal)


def annotate(change: EffectChange, preview: CapturePreview) -> None:
    """Extension goes in the very same accepted-event put, not a second canonical ledger."""
    if change.duplicate_of is not None:
        return
    puts = []
    for record, expected in change.puts:
        if record.address == change.ref:
            event = accepted_from_payload(record.address, record.schema, record.payload)
            check_event_id(event.event_id, preview.request, preview.placement)
            event = replace(event, extras=JsonObject({**event.extras,
                METADATA: metadata(preview.request, preview.placement)}))
            record = replace(record, payload=accepted_to_payload(event))
        puts.append((record, expected))
    change.puts = tuple(puts)


class CaptureLibrary:
    """Trusted headless adapter; pass current controls explicitly on each acceptance.

    `preview` is a director workflow, not a model tool. Completion/adoption use this adapter too:
    generic effect recovery cannot enforce party capacity or exclusive trainer association.
    """
    def __init__(self, store, handle, *, issued, clock, new_id, owner=None):
        self.store = store
        self._config = dict(issued=issued, clock=clock, new_id=new_id, owner=owner)
        self.handle = handle
        self.effects = TurnEffectsLibrary(store, handle, **self._config)

    def _effects_for(self, preview: CapturePreview) -> TurnEffectsLibrary:
        def allocate(prefix):
            return event_id(preview.request, preview.placement) if prefix == 'e' else self._config['new_id'](prefix)
        return TurnEffectsLibrary(self.store, self.handle, **{**self._config, 'new_id': allocate})

    def _recorded(self, request: CaptureRequest) -> CapturePreview | None:
        scan = self.store.campaign_accepted(request.chat.campaign_id)
        if scan.problems:
            raise CaptureRefused('unreadable_acceptances')
        matches = []
        for ref, stored in scan.records.items():
            event = accepted_from_payload(ref, stored.record.schema, stored.record.payload)
            saved = recorded(event)
            if saved is None or saved.request.attempt_id != request.attempt_id:
                continue
            if saved.request != request:
                raise CaptureRefused('attempt_conflict')
            if not event.active:
                raise CaptureRefused('attempt_revoked')
            matches.append(saved)
        if len(matches) > 1:
            raise CaptureRefused('attempt_conflict')
        return matches[0] if matches else None

    def _relations(self, campaign: str):
        scan = self.store.campaign_relations(campaign)
        if scan.problems:
            raise CaptureRefused('unreadable_relations')
        relations = []
        for ref, stored in scan.records.items():
            try:
                relation = relation_from_payload(ref, stored.record.schema, stored.record.payload)
            except ValueError:
                raise CaptureRefused('unreadable_relations') from None
            if not isinstance(relation, Relation):
                raise CaptureRefused('unsupported_relation')
            relations.append(relation)
        return relations, tuple(sorted(((ref, v.etag) for ref, v in scan.records.items()), key=lambda x: str(x[0])))

    def _endpoints(self, request):
        known = set(self.store.campaign_instances(request.chat.campaign_id))
        involved = {request.trainer, request.creature}
        if not involved <= known:
            raise CaptureRefused('missing_instance')
        if involved & paired_instances(known):
            raise CaptureRefused('instance_id_conflict')
        return tuple((ref, self.store.read(ref).etag) for ref in sorted(involved, key=str))

    def _resolve(self, request, *, exclude=(), fixed=None):
        endpoints = self._endpoints(request)
        relations, pins = self._relations(request.chat.campaign_id)
        if request.outcome != 'success':
            return CapturePreview(request, None, proposal_for(request, None), endpoints, pins)
        remaining = [r for r in relations if r.ref not in exclude]
        relevant = [r for r in remaining if r.predicate in (*MEMBERSHIP, 'trained_by') and r.retraction is None]
        if any(r.since.sequence > request.at.sequence or
               (r.ending is not None and r.ending.until.sequence > request.at.sequence) for r in relevant):
            raise CaptureRefused('out_of_order')
        live = [r for r in relevant if r.holds_at(request.at.sequence)]
        if any(r.predicate == 'trained_by' and r.subject == request.creature for r in live):
            raise CaptureRefused('already_trained')
        state = PartyState.build(request.chat.campaign_id, remaining, as_of=request.at.sequence, unreadable=False)
        if state.problems or state.uncertain(request.trainer):
            raise CaptureRefused('ambiguous_party')
        if state.membership(request.creature) is not None:
            raise CaptureRefused('already_member')
        if request.creature in state.anchors_above(request.trainer):
            raise CaptureRefused('party_cycle')
        active, _ = state.party(request.trainer)
        placement = fixed or request.placement
        if placement == 'auto':
            placement = 'party' if len(active) < request.capacity else 'reserve'
        if placement == 'party':
            if len(active) >= request.capacity:
                raise CaptureRefused('party_full')
            if state.reserved(request.trainer):
                raise CaptureRefused('trainer_in_reserve')
        return CapturePreview(request, placement, proposal_for(request, placement), endpoints, pins)

    def preview(self, request: CaptureRequest) -> CapturePreview:
        if type(request) is not CaptureRequest:
            raise CaptureRefused('invalid_request')
        with self.store.lock:
            saved = self._recorded(request)
            return saved if saved is not None else self._resolve(request)

    def _pin(self, change: EffectChange, preview: CapturePreview) -> None:
        previous = change.rests_on
        def unchanged():
            reasons = [] if previous is None else previous()
            try:
                if self._endpoints(preview.request) != preview.endpoint_pins:
                    reasons.append(('capture', 'endpoints changed'))
                if self._relations(preview.request.chat.campaign_id)[1] != preview.relation_pins:
                    reasons.append(('capture', 'relations changed'))
            except (CaptureRefused, StoreError, LibraryPathError):
                reasons.append(('capture', 'basis unreadable'))
            return reasons
        change.rests_on = unchanged

    def accept(self, preview: CapturePreview, *, controls: Iterable, contributions=(), endorsements=()) -> CaptureChange:
        if type(preview) is not CapturePreview:
            raise CaptureRefused('invalid_preview')
        with self.store.lock:
            saved = self._recorded(preview.request)
            current = saved if saved is not None else self._resolve(preview.request)
            if (current.request, current.placement, current.proposal) != (
                    preview.request, preview.placement, preview.proposal):
                raise StaleWrite([('capture', 'decision changed')])
            if saved is None and (current.endpoint_pins, current.relation_pins) != (
                    preview.endpoint_pins, preview.relation_pins):
                raise StaleWrite([('capture', 'reviewed basis changed')])
            if current.proposal is None:
                return CaptureChange(current, None)
            change = self._effects_for(current).accept(current.proposal, controls=controls,
                                                     contributions=contributions, endorsements=endorsements)
            if change.duplicate_of is not None and saved is None:
                raise CaptureRefused('invalid_capture_metadata')
            annotate(change, current)
            if change.duplicate_of is None:
                self._pin(change, current)
            return CaptureChange(current, change)

    def complete(self, ref: AcceptedRef, *, expected: str) -> CaptureChange:
        """Complete an event-present partial capture, preserving its recorded placement.

        A conflicting hand edit is never overridden. Use the generic latest-first revoke
        workflow to abort, with its existing player undo approvals.
        """
        with self.store.lock:
            event, _ = self.effects.read(ref, requested_by=DIRECTOR)
            preview = recorded(event)
            if preview is None:
                raise CaptureRefused('not_a_capture')
            self._recorded(preview.request)  # rejects revoked or ambiguous attempt history
            change = EffectRecovery(self.effects).complete(ref, expected=expected)
            if change.duplicate_of is None:
                current = self._resolve(preview.request, exclude={r.relation for r in event.receipts},
                                        fixed=preview.placement)
                self._pin(change, current)
            return CaptureChange(preview, change)

    def adopt(self, preview: CapturePreview, *, controls: Iterable,
              contributions=(), endorsements=()) -> CaptureChange:
        """Adopt orphaned capture writes using the retained preview, even after restart.

        Generic recovery proves which receipts belong to the orphan. Its provenance ID
        additionally binds ALL capture metadata, not only the generic effect fingerprint.
        """
        if type(preview) is not CapturePreview or preview.proposal is None:
            raise CaptureRefused('invalid_preview')
        if proposal_for(preview.request, preview.placement) != preview.proposal:
            raise CaptureRefused('invalid_preview')
        with self.store.lock:
            saved = self._recorded(preview.request)
            if saved is not None:
                if (saved.placement, saved.proposal) != (preview.placement, preview.proposal):
                    raise CaptureRefused('attempt_conflict')
                return self.accept(saved, controls=controls, contributions=contributions,
                                   endorsements=endorsements)
            change = EffectRecovery(self._effects_for(preview)).adopt(
                preview.proposal, controls=controls, contributions=contributions, endorsements=endorsements)
            check_event_id(change.ref.event_id, preview.request, preview.placement)
            if change.duplicate_of is not None:
                # A generic event without capture metadata must not masquerade as a capture retry.
                raise CaptureRefused('invalid_capture_metadata')
            event_record = next(record for record, _ in change.puts if record.address == change.ref)
            event = accepted_from_payload(change.ref, event_record.schema, event_record.payload)
            current = self._resolve(preview.request, exclude={r.relation for r in event.receipts},
                                    fixed=preview.placement)
            annotate(change, current)
            self._pin(change, current)
            return CaptureChange(current, change)
