"""Durable, authored rumor queue over ordinary 020 evidence."""
from dataclasses import asdict, replace
from collections.abc import Mapping
from grimoire.core.authority import DIRECTOR, AuthorityError, PlayerContribution, controller_of, submit_contribution
from grimoire.core.identity import InstanceRef, paired_instances, validate_segment
from grimoire.core.knowledge import Moment, Proposition, Report
from grimoire.core.perspective import SentReport, controller_knowledge
from grimoire.evidencestore import EvidenceLibrary
from grimoire.ledgers import SovereigntyLedger
from grimoire.ledgers.codec import envelope
from grimoire.rumors import AuthoredSend, Rumor, DeliveryConditions
from .codec import encode
from grimoire.security import is_human
from grimoire.store.errors import LedgerInvalid, TakenIdsUnlisted
from .replay import replay
from .records import output_id as _id, output as _output, expected as _expected



def rumor_actor_ids(store, campaign):
    validate_segment(campaign, 'campaign')
    docs, unread = store.ledger_documents(f'rumors/{campaign}')
    if unread:
        raise TakenIdsUnlisted('rumor ledger cannot be read', unread)
    found, stack = set(), list(docs)
    while stack:
        x = stack.pop()
        if isinstance(x, Mapping):
            for k, v in x.items():
                if k in ('sender', 'recipient', 'attributed_to') and v is not None:
                    try:
                        ref = InstanceRef.parse(v)
                        if ref.campaign_id != campaign:
                            raise ValueError()
                        found.add(ref.instance_id)
                    except (TypeError, ValueError):
                        raise TakenIdsUnlisted('rumor actor cannot be read', ()) from None
                stack.append(v)
        elif isinstance(x, (list, tuple)):
            stack.extend(x)
    return frozenset(found)


class RumorLibrary:
    def __init__(self, store, authority, handle):
        if getattr(authority, '_store', None) is not store:
            raise AuthorityError('invalid_authority')
        self.store, self.authority, self.handle = store, authority, handle

    def _trusted(self):
        if not self.authority.issued(self.handle) or self.handle.trusted is not True:
            raise AuthorityError('untrusted_handle')

    def _director(self, requested_by):
        self._trusted()
        if requested_by != DIRECTOR or not self.handle.principal.startswith('app:'):
            raise AuthorityError('director_required')

    def _load(self, campaign):
        validate_segment(campaign, 'campaign')
        ledger = self.authority.read_ledger(f'rumors/{campaign}')
        return replay(campaign, ledger.entries), ledger.head

    def _write(self, campaign, head, kind, **fields):
        return self.authority._append(f'rumors/{campaign}', [envelope(kind, self.authority.now(), **fields)], head)

    def _author(self, r, requested_by, contribution):
        self._trusted()
        present = set(self.store.campaign_instances(r.sender.campaign_id))
        actors = {r.sender, r.recipient} | ({r.attributed_to} if r.attributed_to else set())
        if actors - present or actors & paired_instances(present):
            raise AuthorityError('unknown_actor')
        controls = SovereigntyLedger(self.authority).load(r.sender.campaign_id).controls
        controller = controller_of(r.sender.campaign_id, r.sender.instance_id, controls)
        principal = self.handle.principal
        if controller == DIRECTOR:
            if requested_by != DIRECTOR or not principal.startswith('app:') or contribution is not None:
                raise AuthorityError('wrong_controller')
        else:
            if not is_human(principal) or principal != controller or requested_by != controller or type(contribution) is not PlayerContribution:
                raise AuthorityError('wrong_controller')
            if (contribution.campaign_id, contribution.actor_id, contribution.submitted_by, contribution.kind, contribution.text, contribution.scene_id, contribution.turn_id) != (r.sender.campaign_id, r.sender.instance_id, controller, 'speech', r.text, r.authored_at.scene_id, r.authored_at.turn_id):
                raise AuthorityError('invalid_contribution')
            submit_contribution(contribution, controls)

    def _source(self, r, at):
        present = set(self.store.campaign_instances(r.sender.campaign_id))
        actors = {r.sender, r.recipient} | ({r.attributed_to} if r.attributed_to else set())
        if actors - present or actors & paired_instances(present):
            raise AuthorityError('unknown_actor')
        ev = EvidenceLibrary(self.store)
        raw = ev.read(r.sender.campaign_id, requested_by=DIRECTOR)
        if raw.quarantined or raw.problems:
            raise ValueError('evidence_unavailable')
        if r.source_evidence_id not in {e.evidence_id for e in ev.actor_knowledge(r.sender.campaign_id, r.sender, as_of=at.sequence).evidence}:
            raise ValueError('unavailable_source')

    def enqueue(self, r, *, moment, requested_by, contribution=None):
        if type(r) is not Rumor or type(moment) is not Moment or moment.sequence < r.authored_at.sequence:
            raise ValueError('invalid_record')
        c = r.sender.campaign_id
        with self.authority.lock:
            (rows, clock, _), head = self._load(c)
            if r.communication_id in rows:
                old = rows[r.communication_id]
                if old['rumor'] != r or old['principal'] != self.handle.principal or old['contribution'] != (asdict(contribution) if contribution else None) or requested_by != (DIRECTOR if self.handle.principal.startswith('app:') else self.handle.principal):
                    raise ValueError('operation_conflict')
                self._trusted()
            else:
                self._author(r, requested_by, contribution)
                if clock is not None and clock != (r.clock_id, r.clock_unit):
                    raise ValueError('clock_mismatch')
                self._source(r, r.authored_at)
                # Reject unstorable or invalid output before reserving the communication id.
                EvidenceLibrary(self.store, self.handle).append(c, [_output(r, 'enqueue', moment)])
                head = self._write(c, head, 'request', value=encode(r), principal=self.handle.principal, contribution=asdict(contribution) if contribution else None)
            return self._resume(c, r.communication_id, 'enqueue', moment, head)

    def _resume(self, campaign, rid, phase, supplied, head=None, *, allow_write=True):
        (rows, _, _), current = self._load(campaign)
        head = current
        row = rows[rid]
        if row['enqueued' if phase == 'enqueue' else 'delivered']:
            return True
        r = row['rumor']
        attempts = row['attempts'][phase]
        evidence = EvidenceLibrary(self.store, self.handle)
        assembly, scan, files = evidence._assembly(campaign)
        if not assembly.clean or scan.problems:
            raise ValueError('evidence_unavailable')
        def output(at):
            return _output(r, phase, at, row['attempts']['enqueue'][-1] if phase == 'delivery' else None)
        if attempts:
            expected = output(attempts[-1])
            name = ('proposition:' if phase == 'enqueue' else 'evidence:') + (expected.proposition_id if phase == 'enqueue' else expected.evidence_id)
            found = assembly.records.get(name)
            if found is not None:
                if found != expected:
                    raise ValueError('evidence_conflict')
                self._write(campaign, head, 'complete', id=rid, phase=phase)
                return True
            if any(ref.name == name for ref in files):
                raise ValueError('evidence_conflict')
        if not allow_write:
            return False
        if phase == 'enqueue':
            self._source(r, r.authored_at)
        if not attempts or attempts[-1].sequence < assembly.high_water:
            if attempts and supplied.sequence <= attempts[-1].sequence:
                raise ValueError('fresh_moment_required')
            if supplied.sequence < assembly.high_water:
                raise ValueError('fresh_moment_required')
            change = evidence.append(campaign, [output(supplied)])
            head = self._write(campaign, head, 'attempt', id=rid, phase=phase, moment=asdict(supplied), reason='stale' if attempts else 'initial', expected=_expected(output(supplied)))
        else:
            change = evidence.append(campaign, [output(attempts[-1])])
        change.commit()
        self._write(campaign, head, 'complete', id=rid, phase=phase)
        return True

    def advance(self, campaign, *, clock_id, clock_unit, tick, moment, conditions, requested_by, limit=100):
        self._director(requested_by)
        if type(tick) is not int or tick < 0 or type(moment) is not Moment or type(conditions) is not DeliveryConditions or type(limit) is not int or not 1 <= limit <= 500:
            raise ValueError('invalid_advance')
        with self.authority.lock:
            (rows, clock, watermark), head = self._load(campaign)
            if clock != (clock_id, clock_unit):
                raise ValueError('clock_mismatch')
            if tick < watermark:
                raise ValueError('clock_backwards')
            self._write(campaign, head, 'advance', clock_id=clock_id, clock_unit=clock_unit, tick=tick)
            pending = sorted((row for row in rows.values() if row['enqueued'] and not row['delivered']), key=lambda row: (row['rumor'].due_tick, row['rumor'].communication_id))
            eligible = [row for row in pending if row['rumor'].due_tick <= tick and conditions.allows(row['rumor'].route)]
            # Reconcile already-written reports even if their route is now blocked.
            recover = [row for row in pending if row not in eligible and row['attempts']['delivery']]
            delivered = []
            for row in (eligible + recover)[:limit]:
                r = row['rumor']
                allowed = row in eligible
                if self._resume(campaign, r.communication_id, 'delivery', moment, allow_write=allowed):
                    delivered.append(r.communication_id)
            return {'delivered': tuple(delivered), 'pending': sum(not v['delivered'] for v in self._load(campaign)[0][0].values())}

    def _actor_reader(self, campaign, actor, requested_by):
        self._trusted()
        if type(actor) is not InstanceRef or actor.campaign_id != campaign:
            raise AuthorityError('campaign_mismatch')
        present = set(self.store.campaign_instances(campaign))
        if actor not in present or actor in paired_instances(present):
            raise AuthorityError('unknown_actor')
        if requested_by == DIRECTOR:
            self._director(requested_by)
        elif (requested_by != self.handle.principal or not is_human(requested_by)
              or controller_of(campaign, actor.instance_id, SovereigntyLedger(self.authority).load(campaign).controls) != requested_by):
            raise AuthorityError('wrong_controller')

    def outbox(self, actor, *, requested_by):
        with self.authority.lock:
            self._actor_reader(actor.campaign_id, actor, requested_by)
            rows = self._load(actor.campaign_id)[0][0]
            return tuple(AuthoredSend(r.communication_id, r.recipient, r.source_evidence_id, r.text, r.channel, r.attributed_to, r.confidence, r.authored_at) for _, row in sorted(rows.items()) if (r := row['rumor']).sender == actor and row['enqueued'])

    def trace(self, campaign, *, requested_by):
        self._director(requested_by)
        with self.authority.lock:
            return self._load(campaign)[0]

    def actor_knowledge(self, campaign, actor, *, as_of, requested_by):
        with self.authority.lock:
            self._actor_reader(campaign, actor, requested_by)
            rows = self._load(campaign)[0][0]
            evidence = EvidenceLibrary(self.store)
            if requested_by == DIRECTOR:
                view = evidence.actor_knowledge(campaign, actor, as_of=as_of)
            else:
                knowledge = evidence.read(campaign, requested_by=DIRECTOR).knowledge
                controls = SovereigntyLedger(self.authority).load(campaign).controls
                view = controller_knowledge(knowledge, actor, requested_by, controls, as_of=as_of)
            ids = {_id(campaign, rid, 'report') for rid in rows}
            sent = [s for s in view.sent if s.evidence_id not in ids]
            for rid, row in rows.items():
                r = row['rumor']
                if row['enqueued'] and r.sender == actor:
                    at = row['attempts']['enqueue'][-1]
                    if at.sequence <= as_of:
                        sent.append(SentReport(_id(campaign, rid, 'report'), r.recipient, _id(campaign, rid, 'proposition'), r.text, r.channel, at))
            return replace(view, sent=tuple(sorted(sent, key=lambda s: (s.sent_at.sequence, s.evidence_id))))
