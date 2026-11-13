"""Durable actor-scoped memory operations over the authority ledger."""
from collections.abc import Mapping
from grimoire.core.authority import DIRECTOR, AuthorityError, controller_of
from grimoire.core.identity import InstanceRef, paired_instances, validate_segment
from grimoire.evidencestore import EvidenceLibrary
from grimoire.ledgers import SovereigntyLedger
from grimoire.ledgers.codec import envelope, exact
from grimoire.memory import MemorySelection, MemoryCorrection, consolidate_view
from grimoire.memorystore.codec import encode, decode
from grimoire.memory.view import _check_acyclic
from grimoire.security import is_human
from grimoire.security.policy import is_valid_principal
from grimoire.store.errors import LedgerInvalid, TakenIdsUnlisted


def memory_actor_ids(store, campaign_id):
    validate_segment(campaign_id, 'campaign_id')
    documents, unread = store.ledger_documents(f'memory/{campaign_id}')
    if unread:
        raise TakenIdsUnlisted('memory ledger cannot be read', unread)
    result, stack = set(), list(documents)
    while stack:
        value = stack.pop()
        if isinstance(value, Mapping):
            if 'actor' in value:
                actor = value['actor']
                try:
                    ref = InstanceRef.parse(actor)
                    if ref.campaign_id != campaign_id:
                        raise ValueError()
                    result.add(ref.instance_id)
                except (TypeError, ValueError):
                    raise TakenIdsUnlisted('memory actor cannot be read', ()) from None
            stack.extend(value.values())
        elif isinstance(value, (list, tuple)):
            stack.extend(value)
    return frozenset(result)


class MemoryLibrary:
    def __init__(self, store, authority, handle):
        if getattr(authority, '_store', None) is not store:
            raise AuthorityError('invalid_authority')
        self.store, self.authority, self.handle = store, authority, handle

    def _editor(self, actor, requested_by):
        h = self.handle
        if not (self.authority.issued(h) and getattr(h, 'trusted', False) is True ):
            raise AuthorityError('untrusted_handle')
        present = self.store.campaign_instances(actor.campaign_id)
        if actor not in present or actor in paired_instances(present):
            raise AuthorityError('unknown_actor')
        controller = controller_of(actor.campaign_id, actor.instance_id, SovereigntyLedger(self.authority).load(actor.campaign_id).controls)
        if (controller is None or requested_by != controller
                or (controller == DIRECTOR and not h.principal.startswith('app:'))
                or (controller != DIRECTOR and (not is_human(h.principal) or h.principal != controller))):
            raise AuthorityError('wrong_controller')
        return controller

    def _load(self, campaign):
        validate_segment(campaign, 'campaign_id')
        name = f'memory/{campaign}'
        if name not in self.authority.views:
            ledger = self.authority.read_ledger(name)
            rows = []
            for entry in ledger.entries:
                try:
                    kind = entry.get('kind')
                    if kind not in ('selection', 'correction'):
                        raise ValueError()
                    data = exact(entry, kind, {'editor', 'principal', 'value'})
                    editor, principal = data['editor'], data['principal']
                    if type(editor) is not str or not editor or not is_valid_principal(principal) or (editor == DIRECTOR and not principal.startswith('app:')) or (editor != DIRECTOR and (not is_human(principal) or editor != principal)):
                        raise ValueError()
                    value = decode(kind, data['value'])
                    if value.actor.campaign_id != campaign:
                        raise ValueError()
                    self._check_record(rows, value, editor)
                    rows.append((value, editor, principal))
                except (ValueError, TypeError, KeyError, LedgerInvalid):
                    raise LedgerInvalid('invalid_memory_ledger') from None
            self.authority.views[name] = [rows, ledger.head]
        return self.authority.views[name]

    @staticmethod
    def _check_record(rows, value, editor):
        if any(v.operation_id == value.operation_id for v, _, _ in rows):
            raise ValueError('duplicate_operation')
        own = [v for v, e, _ in rows if v.actor == value.actor and e == editor]
        if any(v.moment.sequence > value.moment.sequence for v in own):
            raise ValueError('backdated')
        if isinstance(value, MemoryCorrection):
            corrected = {i for v in own if isinstance(v, MemoryCorrection) for i in v.obsolete_ids}
            if corrected.intersection(value.obsolete_ids):
                raise ValueError('competing_correction')
            # Subsequent validation in consolidate_view detects cycles.
            links = {i: v.replacement_ids for v in own if isinstance(v, MemoryCorrection) for i in v.obsolete_ids}
            links.update({i: value.replacement_ids for i in value.obsolete_ids})
            _check_acyclic(links, value.obsolete_ids)

    def _write(self, value, requested_by):
        if type(value) not in (MemorySelection, MemoryCorrection):
            raise ValueError('invalid_record')
        with self.authority.lock:
            editor = self._editor(value.actor, requested_by)
            state = self._load(value.actor.campaign_id)
            rows = state[0]
            for old, old_editor, principal in rows:
                if old.operation_id == value.operation_id:
                    if old == value and old_editor == editor and principal == self.handle.principal:
                        return
                    raise ValueError('operation_conflict')
            self._check_record(rows, value, editor)
            knowledge = EvidenceLibrary(self.store).actor_knowledge(value.actor.campaign_id, value.actor, as_of=value.moment.sequence)
            known = {e.evidence_id: e for e in knowledge.evidence}
            ids = value.evidence_ids if isinstance(value, MemorySelection) else value.obsolete_ids + value.replacement_ids
            if any(i not in known for i in ids):
                raise ValueError('unavailable_evidence')
            if isinstance(value, MemoryCorrection) and min(known[i].moment.sequence for i in value.replacement_ids) <= max(known[i].moment.sequence for i in value.obsolete_ids):
                raise ValueError('replacement_not_later')
            kind, payload = encode(value)
            entry = envelope(kind, self.authority.now(), editor=editor, principal=self.handle.principal, value=payload)
            state[1] = self.authority._append(f'memory/{value.actor.campaign_id}', [entry], state[1])
            rows.append((value, editor, self.handle.principal))

    def select(self, selection, *, requested_by):
        self._write(selection, requested_by)

    def correct(self, correction, *, requested_by):
        self._write(correction, requested_by)

    def view(self, actor, collection_id, *, as_of, limit=100, requested_by):
        with self.authority.lock:
            editor = self._editor(actor, requested_by)
            rows = self._load(actor.campaign_id)[0]
            values = [v for v, e, _ in rows if v.actor == actor and e == editor]
            knowledge = EvidenceLibrary(self.store).actor_knowledge(actor.campaign_id, actor, as_of=as_of)
            return consolidate_view(knowledge, collection_id, (v for v in values if isinstance(v, MemorySelection)),
                                    (v for v in values if isinstance(v, MemoryCorrection)), limit=limit)

    def inspect(self, campaign_id, *, requested_by):
        with self.authority.lock:
            h = self.handle
            if requested_by != DIRECTOR or not (self.authority.issued(h) and getattr(h, 'trusted', False) is True and h.principal.startswith('app:')):
                raise AuthorityError('director_required')
            return tuple(self._load(campaign_id)[0])
