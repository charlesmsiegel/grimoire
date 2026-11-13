"""Deterministic expected evidence for a queue attempt."""
from hashlib import sha256
from grimoire.core.knowledge import Proposition, Report
from grimoire.evidence import encode


def output_id(campaign, communication, phase):
    return 'rumor-' + sha256(('\0'.join((campaign, communication, phase))).encode()).hexdigest()


def output(r, phase, at, sent_at=None):
    c, rid = r.sender.campaign_id, r.communication_id
    if phase == 'enqueue':
        return Proposition(c, output_id(c, rid, 'proposition'), r.text, at, r.provenance)
    return Report(c, output_id(c, rid, 'report'), r.recipient, output_id(c, rid, 'proposition'), r.channel, at, r.provenance, True, r.sender, r.attributed_to, True, sent_at)


def expected(record):
    ref, schema, payload = encode(record)
    return {'name': ref.name, 'schema': schema, 'payload': payload}
