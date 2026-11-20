"""Deterministic expected evidence for a queue attempt."""
from hashlib import sha256
from grimoire.core.knowledge import Proposition, Report
from grimoire.evidence import encode


PREFIX = 'rumor-'


def output_id(campaign, communication, phase):
    return PREFIX + sha256(('\0'.join((campaign, communication, phase))).encode()).hexdigest()


def is_rumor_output(record_id):
    """Whether ``record_id`` has the shape of a rumor queue's output id (64 hex digits after the prefix)."""
    tail = record_id[len(PREFIX):]
    return record_id.startswith(PREFIX) and len(tail) == 64 and all(c in '0123456789abcdef' for c in tail)


def output(r, phase, at, sent_at=None):
    c, rid = r.sender.campaign_id, r.communication_id
    if phase == 'enqueue':
        return Proposition(c, output_id(c, rid, 'proposition'), r.text, at, r.provenance)
    return Report(c, output_id(c, rid, 'report'), r.recipient, output_id(c, rid, 'proposition'), r.channel, at, r.provenance, True, r.sender, r.attributed_to, True, sent_at)


def expected(record):
    ref, schema, payload = encode(record)
    return {'name': ref.name, 'schema': schema, 'payload': payload}
