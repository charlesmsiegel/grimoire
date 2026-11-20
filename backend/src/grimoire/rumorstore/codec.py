from collections.abc import Mapping
"""Strict serialized queue values."""
from dataclasses import asdict
from grimoire.core.identity import InstanceRef
from grimoire.core.knowledge import Moment
from grimoire.store.errors import LedgerInvalid
from grimoire.rumors import DeliveryConditions, Rumor


def encode(value):
    data = asdict(value)
    for key in ('sender', 'recipient', 'attributed_to'):
        data[key] = str(getattr(value, key)) if getattr(value, key) is not None else None
    return data


def moment(data):
    if not isinstance(data, Mapping) or set(data) != {'sequence', 'scene_id', 'turn_id'}:
        raise ValueError('invalid_moment')
    return Moment(**data)


def decode(data):
    try:
        if not isinstance(data, Mapping) or set(data) != set(Rumor.__dataclass_fields__):
            raise ValueError()
        d = dict(data)
        for key in ('sender', 'recipient', 'attributed_to'):
            d[key] = InstanceRef.parse(d[key]) if d[key] is not None else None
        d['authored_at'] = moment(d['authored_at'])
        if not isinstance(d['route'], Mapping) or set(d['route']) != set(DeliveryConditions.__dataclass_fields__):
            raise ValueError()
        d['route'] = DeliveryConditions(**d['route'])
        return Rumor(**d)
    except (ValueError, TypeError, KeyError):
        raise LedgerInvalid('invalid_rumor_record') from None
