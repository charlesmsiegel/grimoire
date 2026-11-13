"""Pure strict dependency declaration wire; archives have a separate schema."""
from collections.abc import Mapping
from .model import Dependency, Pin

DEPENDENCY_SCHEMA = 'grimoire.continuity-dependency/1'


def decode_dependency(schema, payload):
    """Strict immutable declaration wire; archives are a different schema."""
    if schema != DEPENDENCY_SCHEMA:
        raise ValueError('unsupported continuity schema')
    if not isinstance(payload, Mapping) or set(payload) != {'source', 'dependent', 'reason', 'certainty'}:
        raise ValueError('malformed dependency')
    pins = []
    for key in ('source', 'dependent'):
        value = payload[key]
        if not isinstance(value, Mapping) or set(value) != {'path', 'revision'}:
            raise ValueError('malformed dependency endpoint')
        pins.append(Pin(value['path'], value['revision']))
    edge = Dependency(*pins, payload['reason'], payload['certainty'])
    if edge.certainty != 'known' or edge.source.campaign_id != edge.dependent.campaign_id:
        raise ValueError('declarations require known same-campaign dependence')
    return edge
