"""Rebuild and validate queue state from the append-only authority ledger."""
from grimoire.ledgers.codec import exact
from .codec import campaign_time, decode, moment
from grimoire.store.errors import LedgerInvalid
from grimoire.security.policy import is_valid_principal
from .records import output, expected
from grimoire.security import is_human
from grimoire.core.authority import PlayerContribution
from collections.abc import Mapping


def replay(campaign, entries):
    """``(rows, watermark)``: each communication's request, attempts and completions, and the newest campaign
    time an ``advance`` delivered at (``None`` before the first)."""
    rows, watermark = {}, None
    try:
        for entry in entries:
            kind = entry.get('kind')
            if kind == 'request':
                d = exact(entry, kind, {'value', 'principal', 'contribution'})
                r = decode(d['value'])
                p = d['principal']
                if r.sender.campaign_id != campaign or r.communication_id in rows or not is_valid_principal(p):
                    raise ValueError()
                if not (p.startswith('app:') or is_human(p)):
                    raise ValueError()
                proof = d['contribution']
                if p.startswith('app:'):
                    if proof is not None:
                        raise ValueError()
                else:
                    if not isinstance(proof, Mapping) or set(proof) != set(PlayerContribution.__dataclass_fields__):
                        raise ValueError()
                    contribution = PlayerContribution(**proof)
                    if (contribution.campaign_id, contribution.actor_id, contribution.submitted_by, contribution.kind, contribution.text, contribution.scene_id, contribution.turn_id) != (campaign, r.sender.instance_id, p, 'speech', r.text, r.authored_at.scene_id, r.authored_at.turn_id):
                        raise ValueError()
                rows[r.communication_id] = {'rumor': r, 'principal': p, 'contribution': d['contribution'], 'attempts': {'enqueue': [], 'delivery': []}, 'enqueued': False, 'delivered': False}
            elif kind == 'attempt':
                d = exact(entry, kind, {'id', 'phase', 'moment', 'reason', 'expected'})
                row = rows[d['id']]
                phase = d['phase']
                if phase not in ('enqueue', 'delivery') or row['delivered'] or (phase == 'enqueue' and row['enqueued']) or (phase == 'delivery' and not row['enqueued']):
                    raise ValueError()
                at = moment(d['moment'])
                attempts = row['attempts'][phase]
                if d['reason'] != ('initial' if not attempts else 'stale') or (attempts and at.sequence <= attempts[-1].sequence):
                    raise ValueError()
                if phase == 'enqueue' and at.sequence < row['rumor'].authored_at.sequence:
                    raise ValueError()
                if phase == 'delivery' and at.sequence < row['attempts']['enqueue'][-1].sequence:
                    raise ValueError()
                recorded = output(row['rumor'], phase, at, row['attempts']['enqueue'][-1] if phase == 'delivery' else None)
                if d['expected'] != expected(recorded):
                    raise ValueError()
                attempts.append(at)
            elif kind == 'complete':
                d = exact(entry, kind, {'id', 'phase'})
                row = rows[d['id']]
                phase = d['phase']
                if phase not in ('enqueue', 'delivery') or not row['attempts'][phase] or row['delivered'] or (phase == 'enqueue' and row['enqueued']) or (phase == 'delivery' and not row['enqueued']):
                    raise ValueError()
                row['enqueued' if phase == 'enqueue' else 'delivered'] = True
            elif kind == 'advance':
                d = exact(entry, kind, {'at'})
                at = campaign_time(d['at'])
                if watermark is not None and at < watermark:
                    raise ValueError()
                watermark = at
            else:
                raise ValueError()
    except (KeyError, ValueError, TypeError, LedgerInvalid):
        raise LedgerInvalid('invalid_rumor_ledger') from None
    return rows, watermark
